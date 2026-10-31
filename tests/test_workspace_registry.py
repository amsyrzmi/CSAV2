"""Schema contract for the workspace assistant: proposals, directory view, relaxed turns."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.console.contracts import ConsoleActor, MembershipOut, Role
from csa.core import crypto
from csa.core.tenancy import tx
from csa.decide.groq import GroqClient
from csa.email.api import build_email_router
from csa.email.gmail import GmailClient
from csa.email.models import DraftOut, DraftProposal, draft_version
from csa.email.service import EmailService
from csa.hr.api import build_hr_router
from csa.hr.assistant import HrAssistant
from csa.hr.service import HrService
from csa.workspace import registry as registry_module
from csa.workspace.contracts import CardField, ProposalKind
from csa.workspace.executors import build_proposal_registry
from csa.workspace.planner import WorkspacePlanner
from csa.workspace.registry import (
    CLAIM_LEASE,
    CardContent,
    ProposalError,
    ProposalOrigin,
    ProposalRegistry,
)
from fastapi import HTTPException
from psycopg.rows import dict_row
from pydantic import SecretStr
from starlette.requests import Request

from tests.conftest import requires_docker
from tests.test_console_inbox import held, seed
from tests.test_console_services import activate
from tests.test_console_sessions import MEMBER_PASSWORD, ORIGIN, SessionWorld
from tests.test_email_service import FakeGmail, ScriptedModel

EMAIL_REDIRECT = f"{ORIGIN}/console/v1/email/oauth/callback"

pytest_plugins = ["tests.test_console_sessions"]

pytestmark = requires_docker

INSERT_PROPOSAL = (
    "INSERT INTO console_proposals (client_id,user_id,login_session_id,kind,payload,"
    "payload_sha256,card,origin,status,expires_at) "
    "VALUES (%s,%s,%s,%s,'{}',%s,'{}','tab','requested',now()+interval '1 minute')"
)
TURN_COLUMNS = (
    "INSERT INTO console_workspace_turns (client_id,thread_id,owner_user_id,session_id,"
    "request_key,fingerprint,body,action,page_key,result) "
)


async def test_proposals_table_rls_grants_and_checks(sessions: SessionWorld) -> None:
    t = sessions.tenants[0]
    rows = await sessions.q(
        "SELECT has_table_privilege('csa_ro','public.console_proposals','SELECT') r,"
        " has_table_privilege('csa_app','public.console_proposals','UPDATE') u,"
        " has_table_privilege('csa_identity','public.console_proposals','SELECT') i,"
        " (SELECT relrowsecurity FROM pg_class WHERE oid='public.console_proposals'::regclass) rls"
    )
    assert rows[0]["r"] is False and rows[0]["u"] is True
    assert rows[0]["i"] is False and rows[0]["rls"] is True
    with pytest.raises(Exception, match="console_proposals_kind_check|violates check"):
        await sessions.q(INSERT_PROPOSAL, t, sessions.admin_id, uuid4(), "nope", "0" * 64)
    with pytest.raises(Exception, match="payload_sha256|violates check"):
        await sessions.q(INSERT_PROPOSAL, t, sessions.admin_id, uuid4(), "email_export", "xyz")
    with pytest.raises(Exception, match="console_proposals_origin_check"):
        await sessions.q(
            INSERT_PROPOSAL.replace("'tab'", "'planner'"),
            t, sessions.admin_id, uuid4(), "email_export", "0" * 64,
        )  # fmt: skip
    with pytest.raises(Exception, match="origin"):  # NOT NULL
        await sessions.q(
            INSERT_PROPOSAL.replace("'tab'", "NULL"),
            t, sessions.admin_id, uuid4(), "email_export", "0" * 64,
        )  # fmt: skip
    with pytest.raises(Exception, match="expires_at|violates check"):
        await sessions.q(
            "INSERT INTO console_proposals (client_id,user_id,login_session_id,kind,payload,"
            "payload_sha256,card,origin,status,expires_at) VALUES (%s,%s,%s,'email_export','{}',"
            "%s,'{}','tab','requested',now()+interval '16 minutes')",
            t,
            sessions.admin_id,
            uuid4(),
            "0" * 64,
        )
    with pytest.raises(Exception, match="card|violates check"):
        await sessions.q(
            INSERT_PROPOSAL.replace("'{}','tab'", "'[]','tab'"),
            t, sessions.admin_id, uuid4(), "email_export", "0" * 64,
        )  # fmt: skip
    with pytest.raises(Exception, match="violates check"):
        await sessions.q(
            INSERT_PROPOSAL.replace("'requested'", "'confirmed'"),
            t, sessions.admin_id, uuid4(), "email_export", "0" * 64,
        )  # fmt: skip
    # Valid shape passes CHECKs and is stopped only by the session foreign key.
    with pytest.raises(Exception, match="foreign key"):
        await sessions.q(INSERT_PROPOSAL, t, sessions.admin_id, uuid4(), "email_export", "0" * 64)


async def test_company_directory_view_is_tenant_scoped(
    sessions: SessionWorld, app_dsn: str
) -> None:
    t0, t1 = sessions.tenants
    ids = [uuid4(), uuid4(), uuid4(), uuid4()]
    await sessions.q(
        "INSERT INTO hr_employees (client_id,id,display_name,job_title,work_email,is_active)"
        " VALUES (%s,%s,'Ada Synthetic','Engineer','ada@example.test',true),"
        " (%s,%s,'Bo Synthetic',NULL,NULL,true),"
        " (%s,%s,'Gone Synthetic',NULL,NULL,false),"
        " (%s,%s,'Other Tenant',NULL,NULL,true)",
        t0, ids[0], t0, ids[1], t0, ids[2], t1, ids[3],
    )  # fmt: skip
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT set_config('app.client_id', %s, true)", (t0,))
            await cur.execute("SELECT 1 FROM hr_employees")
            direct = await cur.fetchall()
            await cur.execute("SELECT * FROM company_directory ORDER BY display_name")
            rows = await cur.fetchall()
        async with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT 1 FROM company_directory")
            none = await cur.fetchall()
    assert direct == []  # base table stays hidden without app.hr_user_id
    assert [r["display_name"] for r in rows] == ["Ada Synthetic", "Bo Synthetic"]
    assert {str(r["client_id"]) for r in rows} == {t0}
    assert rows[0]["job_title"] == "Engineer" and rows[0]["work_email"] == "ada@example.test"
    assert set(rows[0]) == {
        "client_id", "employee_id", "user_id", "display_name", "job_title", "work_email",
    }  # fmt: skip
    assert none == []


async def test_workspace_turn_checks_accept_new_actions_and_reject_unknown(
    sessions: SessionWorld,
) -> None:
    for name in ("console_workspace_turns_action_check", "console_workspace_turns_page_key_check"):
        rows = await sessions.q(
            "SELECT count(*) n FROM pg_constraint WHERE conname=%s"
            " AND conrelid='public.console_workspace_turns'::regclass",
            name,
        )
        assert rows[0]["n"] == 1
    rows = await sessions.q(
        "SELECT count(*) n FROM pg_constraint WHERE contype='c'"
        " AND conrelid='public.console_workspace_turns'::regclass"
        " AND pg_get_constraintdef(oid) LIKE '%%resource_id%%'"
    )
    assert rows[0]["n"] == 0
    with pytest.raises(Exception, match="console_workspace_turns_action_check"):
        await sessions.q(
            TURN_COLUMNS + "VALUES (%s,%s,%s,%s,%s,%s,'x','bogus','chat','{}')",
            sessions.tenants[0], uuid4(), sessions.admin_id, uuid4(), uuid4(), "0" * 64,
        )  # fmt: skip
    with pytest.raises(Exception, match="console_workspace_turns_page_key_check"):
        await sessions.q(
            TURN_COLUMNS + "VALUES (%s,%s,%s,%s,%s,%s,'x','navigate','bogus','{}')",
            sessions.tenants[0], uuid4(), sessions.admin_id, uuid4(), uuid4(), "0" * 64,
        )  # fmt: skip


class FakeTrainingExecutor:
    """Registry test double: no HR; records executions and reports a mutable version."""

    kind: ProposalKind = "training_submit"

    def __init__(self, pool: Any = None) -> None:
        self.version: str | None = "v1"
        self.executed: list[dict[str, Any]] = []
        self.pool = pool
        self.held: list[int] = []  # connections checked out of `pool` during each call
        self.gated = 0
        self.gate_error: Exception | None = None
        self.origins: list[tuple[str, str]] = []  # (gate|card|execute, origin) per call
        self.plan: list[tuple[asyncio.Event, bool]] = []  # per execute: (release, fail)
        self.entered: asyncio.Queue[int] = asyncio.Queue()

    def _note_held(self) -> None:
        if self.pool is not None:
            stats = self.pool.get_stats()
            self.held.append(stats.get("pool_size", 0) - stats.get("pool_available", 0))

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        self.gated += 1
        self.origins.append(("gate", origin))
        if self.gate_error is not None:
            raise self.gate_error

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict,
        *,
        origin: ProposalOrigin,
    ) -> tuple[CardContent, str | None]:
        self.origins.append(("card", origin))
        await self.gate(request, actor, client_id, origin=origin)
        content = CardContent(
            title="Submit training request",
            summary=f"Submit {payload['title']}",
            fields=[CardField(label="Title", value=str(payload["title"]))],
        )
        self._note_held()
        return content, self.version

    async def execute(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict,
        *,
        proposal_id: UUID,
        expected_version: str | None,
        origin: ProposalOrigin,
    ) -> dict:
        self.origins.append(("execute", origin))
        self._note_held()
        call = len(self.executed)
        self.executed.append(payload)
        if self.plan:
            release, fail = self.plan.pop(0)
            await self.entered.put(call)
            await release.wait()
            if fail:
                raise RuntimeError(f"synthetic failure of execute {call}")
        return {"receipt": len(self.executed), "title": payload["title"]}


def _request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": []})


async def _session_id(sessions: SessionWorld, user_id: str) -> UUID:
    rows = await sessions.q(
        "SELECT id FROM console_sessions WHERE user_id=%s ORDER BY created_at DESC LIMIT 1",
        user_id,
    )
    return rows[0]["id"]


def _actor(me: dict[str, Any], session_id: UUID) -> ConsoleActor:
    user = me["user"]
    return ConsoleActor(
        user_id=UUID(user["id"]),
        email=user["email"],
        display_name=user["display_name"],
        platform_admin=user["is_platform_admin"],
        must_change_password=user["must_change_password"],
        session_id=session_id,
        client_id=UUID(me["active_client_id"]),
        role=Role(me["role"]),
        memberships=[MembershipOut.model_validate(m) for m in me["memberships"]],
        csrf_hash="synthetic",
    )


async def _member_actor(sessions: SessionWorld) -> tuple[ConsoleActor, dict[str, Any]]:
    _, me = await sessions.member(role="tenant_staff")
    return _actor(me, await _session_id(sessions, me["user"]["id"])), me


def _world() -> tuple[FakeTrainingExecutor, ProposalRegistry]:
    fake = FakeTrainingExecutor()
    return fake, ProposalRegistry({"training_submit": fake})


async def _create(
    sessions: SessionWorld,
    registry: ProposalRegistry,
    actor: ConsoleActor,
    *,
    thread_id: UUID | None = None,
    title: str = "Forklift safety",
    origin: ProposalOrigin = "tab",
) -> Any:
    tenant = sessions.tenants[0]
    return await registry.create(
        sessions.pool,
        _request(),
        actor,
        UUID(tenant),
        "training_submit",
        {"title": title},
        thread_id=thread_id,
        origin=origin,
    )


async def _confirm(sessions: SessionWorld, registry: ProposalRegistry, actor, pid) -> Any:
    tenant = sessions.tenants[0]
    return await registry.confirm(sessions.pool, _request(), actor, UUID(tenant), pid)


async def _row(sessions: SessionWorld, pid: UUID) -> dict[str, Any]:
    return (await sessions.q("SELECT * FROM console_proposals WHERE id=%s", pid))[0]


async def test_create_returns_requested_card_bound_to_login_session(
    sessions: SessionWorld,
) -> None:
    actor, _ = await _member_actor(sessions)
    _, registry = _world()
    out = await _create(sessions, registry, actor)
    assert out.status == "requested" and out.kind == "training_submit"
    assert out.expires_at - out.created_at == timedelta(minutes=15)
    assert out.card.proposal_id == out.id and out.card.expected_version == "v1"
    assert out.card.title == "Submit training request"
    assert [f.value for f in out.card.fields] == ["Forklift safety"]
    assert out.result is None
    row = await _row(sessions, out.id)
    assert row["login_session_id"] == actor.session_id and row["user_id"] == actor.user_id
    assert row["payload"] == {"title": "Forklift safety"}
    canonical = json.dumps({"title": "Forklift safety"}, sort_keys=True, separators=(",", ":"))
    assert row["payload_sha256"] == hashlib.sha256(canonical.encode()).hexdigest()
    assert row["expected_version"] == "v1" and row["thread_id"] is None
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        again = await registry.get(conn, actor, UUID(tenant), out.id)
    assert again == out


async def test_confirm_from_second_login_session_is_rejected(sessions: SessionWorld) -> None:
    actor, me = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    second = await sessions.browser()
    login = await sessions.mutate(
        second,
        "/console/v1/auth/login",
        {"email": me["user"]["email"], "password": MEMBER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    other = _actor(login.json(), await _session_id(sessions, me["user"]["id"]))
    assert other.session_id != actor.session_id
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, other, out.id)
    assert exc.value.status_code == 403 and exc.value.detail == "proposal_session_mismatch"
    assert fake.executed == []
    assert (await _row(sessions, out.id))["status"] == "requested"
    done = await _confirm(sessions, registry, actor, out.id)
    assert done.status == "executed" and fake.executed == [{"title": "Forklift safety"}]


async def test_expired_proposal_is_refused_and_marked_expired(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    await sessions.q(
        "UPDATE console_proposals SET created_at = now() - interval '10 minutes',"
        " expires_at = now() - interval '1 second' WHERE id=%s",
        out.id,
    )
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, actor, out.id)
    assert exc.value.status_code == 409 and exc.value.detail == "proposal_expired"
    assert (await _row(sessions, out.id))["status"] == "expired"
    assert fake.executed == []
    with pytest.raises(ProposalError) as again:
        await _confirm(sessions, registry, actor, out.id)
    assert again.value.status_code == 409 and again.value.detail == "proposal_consumed"


async def test_expiry_write_commits_before_the_refusal_is_raised(
    sessions: SessionWorld,
) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    await sessions.q(
        "UPDATE console_proposals SET created_at = now() - interval '10 minutes',"
        " expires_at = now() - interval '1 second' WHERE id=%s",
        out.id,
    )
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, actor, out.id)
    assert exc.value.detail == "proposal_expired"
    assert (await _row(sessions, out.id))["status"] == "expired"
    audits = await sessions.q(
        "SELECT count(*) n FROM console_audit WHERE target_id=%s"
        " AND action='workspace.proposal.confirmed'",
        str(out.id),
    )
    assert audits[0]["n"] == 0
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        seen = await registry.get(conn, actor, UUID(tenant), out.id)
    assert seen.status == "expired"
    assert (await _row(sessions, out.id))["status"] == "expired"
    assert fake.executed == []


async def test_confirm_executes_once_and_replay_returns_stored_result(
    sessions: SessionWorld,
) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    done = await _confirm(sessions, registry, actor, out.id)
    assert done.status == "executed"
    assert done.result == {"receipt": 1, "title": "Forklift safety"}
    row = await _row(sessions, out.id)
    assert row["status"] == "executed" and row["executed_at"] is not None
    assert row["result"] == done.result
    replay = await _confirm(sessions, registry, actor, out.id)
    assert replay == done
    assert len(fake.executed) == 1
    executed_audits = await sessions.q(
        "SELECT count(*) n FROM console_audit WHERE target_id=%s"
        " AND action='workspace.proposal.executed'",
        str(out.id),
    )
    assert executed_audits[0]["n"] == 1


@pytest.mark.parametrize("origin", ["tab", "assistant"])
async def test_origin_is_stored_and_passed_to_every_executor_call(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch, origin: ProposalOrigin
) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor, origin=origin)
    assert (await _row(sessions, out.id))["origin"] == origin
    _fail_recording_executed_once(monkeypatch)
    with pytest.raises(RuntimeError):
        await _confirm(sessions, registry, actor, out.id)
    await _age_claim(sessions, out.id)
    assert (await _confirm(sessions, registry, actor, out.id)).status == "executed"
    # create: card+gate; confirm: card+gate, execute; stale retake: gate, execute.
    assert [step for step, _ in fake.origins] == [
        "card", "gate", "card", "gate", "execute", "gate", "execute",
    ]  # fmt: skip
    assert {o for _, o in fake.origins} == {origin}


async def test_insert_refuses_an_origin_other_than_the_prepared_one(
    sessions: SessionWorld,
) -> None:
    actor, _ = await _member_actor(sessions)
    _, registry = _world()
    tenant = UUID(sessions.tenants[0])
    prepared = await registry.prepare(
        _request(), actor, tenant, "training_submit", {"title": "X"}, origin="tab"
    )
    async with tx(sessions.pool, str(tenant)) as conn:
        with pytest.raises(ValueError, match="prepared as tab"):
            await registry.insert(conn, actor, tenant, prepared, thread_id=None, origin="assistant")


async def test_stored_card_survives_executor_card_failure(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    done_p = await _create(sessions, registry, actor)
    done = await _confirm(sessions, registry, actor, done_p.id)
    open_p = await _create(sessions, registry, actor, title="Ladder safety")

    async def broken(*_: Any) -> tuple[CardContent, str | None]:
        raise RuntimeError("record gone")

    fake.card = broken  # type: ignore[method-assign]
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        seen = await registry.get(conn, actor, UUID(tenant), done_p.id)
        cancelled = await registry.cancel(conn, actor, UUID(tenant), open_p.id)
    assert seen == done and seen.card == done_p.card
    assert cancelled.status == "cancelled"
    assert cancelled.card == open_p.card
    assert [f.value for f in cancelled.card.fields] == ["Ladder safety"]
    assert (await _row(sessions, done_p.id))["card"]["title"] == "Submit training request"


async def test_executor_failure_rolls_back_to_requested(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)

    async def boom(*_: Any, **__: Any) -> dict:
        raise RuntimeError("synthetic executor failure")

    fake.execute = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="synthetic executor failure"):
        await _confirm(sessions, registry, actor, out.id)
    row = await _row(sessions, out.id)
    assert row["status"] == "requested" and row["result"] is None
    del fake.execute  # restore the class method
    done = await _confirm(sessions, registry, actor, out.id)
    assert done.status == "executed" and len(fake.executed) == 1


async def test_registry_holds_no_connection_while_the_executor_runs(
    sessions: SessionWorld,
) -> None:
    actor, _ = await _member_actor(sessions)
    fake = FakeTrainingExecutor(sessions.pool)
    registry = ProposalRegistry({"training_submit": fake})
    out = await _create(sessions, registry, actor)
    done = await _confirm(sessions, registry, actor, out.id)
    assert done.status == "executed"
    assert fake.held == [0, 0, 0]  # card at create, card at confirm, execute


def _fail_recording_executed_once(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Make the registry's final `executed` write fail once, after the executor ran."""
    real = registry_module._set_status
    failed: list[str] = []

    async def flaky(conn: Any, client_id: UUID, proposal_id: UUID, status: str, result: Any):
        if status == "executed" and not failed:
            failed.append(status)
            raise RuntimeError("synthetic registry commit failure")
        return await real(conn, client_id, proposal_id, status, result)

    monkeypatch.setattr(registry_module, "_set_status", flaky)
    return failed


async def _age_claim(sessions: SessionWorld, pid: UUID | str) -> None:
    """Make a confirmed row's lease stale, as if its confirm died after claiming."""
    await sessions.q(
        "UPDATE console_proposals SET claimed_at = now() - %s - interval '1 second'"
        " WHERE id=%s AND status='confirmed'",
        CLAIM_LEASE,
        pid,
    )


async def test_confirm_interrupted_after_execute_retries_without_a_new_card_check(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    failed = _fail_recording_executed_once(monkeypatch)
    with pytest.raises(RuntimeError, match="synthetic registry commit failure"):
        await _confirm(sessions, registry, actor, out.id)
    assert failed == ["executed"] and len(fake.executed) == 1
    assert (await _row(sessions, out.id))["status"] == "confirmed"
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        with pytest.raises(ProposalError) as exc:
            await registry.cancel(conn, actor, UUID(tenant), out.id)
    assert exc.value.detail == "proposal_consumed"
    with pytest.raises(ProposalError) as live:
        await _confirm(sessions, registry, actor, out.id)  # lease still live
    assert live.value.detail == "proposal_consumed" and len(fake.executed) == 1
    await _age_claim(sessions, out.id)
    fake.version = "v2"  # the record moved because the first run acted; no stale refusal
    gated = fake.gated
    done = await _confirm(sessions, registry, actor, out.id)
    assert fake.gated == gated + 1  # the package gate is re-checked when retaking a claim
    assert done.status == "executed" and len(fake.executed) == 2  # idempotent service replays
    assert (await _confirm(sessions, registry, actor, out.id)) == done
    assert len(fake.executed) == 2


async def test_concurrent_confirms_execute_once(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    release = asyncio.Event()
    fake.plan = [(release, False)]
    first = asyncio.create_task(_confirm(sessions, registry, actor, out.id))
    await asyncio.wait_for(fake.entered.get(), 10)
    with pytest.raises(ProposalError) as loser:
        await _confirm(sessions, registry, actor, out.id)
    assert loser.value.status_code == 409 and loser.value.detail == "proposal_consumed"
    row = await _row(sessions, out.id)
    assert row["status"] == "confirmed" and row["claim_token"] is not None
    release.set()
    done = await asyncio.wait_for(first, 10)
    assert done.status == "executed" and len(fake.executed) == 1
    assert (await _confirm(sessions, registry, actor, out.id)) == done
    assert len(fake.executed) == 1
    row = await _row(sessions, out.id)
    assert row["claim_token"] is None and row["claimed_at"] is None


async def test_failed_execute_reverts_only_its_own_claim(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    first_release, second_release = asyncio.Event(), asyncio.Event()
    fake.plan = [(first_release, True), (second_release, False)]
    first = asyncio.create_task(_confirm(sessions, registry, actor, out.id))
    assert await asyncio.wait_for(fake.entered.get(), 10) == 0
    first_token = (await _row(sessions, out.id))["claim_token"]
    await _age_claim(sessions, out.id)
    second = asyncio.create_task(_confirm(sessions, registry, actor, out.id))
    assert await asyncio.wait_for(fake.entered.get(), 10) == 1
    second_token = (await _row(sessions, out.id))["claim_token"]
    assert second_token not in (None, first_token)
    first_release.set()
    with pytest.raises(RuntimeError, match="synthetic failure of execute 0"):
        await asyncio.wait_for(first, 10)
    row = await _row(sessions, out.id)
    assert row["status"] == "confirmed" and row["claim_token"] == second_token  # not reverted
    second_release.set()
    done = await asyncio.wait_for(second, 10)
    assert done.status == "executed" and done.result == {"receipt": 2, "title": "Forklift safety"}


async def test_record_requires_the_recorders_own_claim(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    first_release, second_release = asyncio.Event(), asyncio.Event()
    fake.plan = [(first_release, False), (second_release, False)]
    first = asyncio.create_task(_confirm(sessions, registry, actor, out.id))
    await asyncio.wait_for(fake.entered.get(), 10)
    await _age_claim(sessions, out.id)
    second = asyncio.create_task(_confirm(sessions, registry, actor, out.id))
    await asyncio.wait_for(fake.entered.get(), 10)
    first_release.set()  # its lease was retaken: it must not record over the live claim
    with pytest.raises(ProposalError) as lost:
        await asyncio.wait_for(first, 10)
    assert lost.value.detail == "proposal_consumed"
    assert (await _row(sessions, out.id))["status"] == "confirmed"
    second_release.set()
    done = await asyncio.wait_for(second, 10)
    assert done.status == "executed" and done.result is not None
    assert done.result["receipt"] == 2
    executed = await sessions.q(
        "SELECT count(*) n FROM console_audit WHERE target_id=%s"
        " AND action='workspace.proposal.executed'",
        str(out.id),
    )
    assert executed[0]["n"] == 1


async def test_retaking_a_stale_claim_rechecks_gate_and_expiry(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    _fail_recording_executed_once(monkeypatch)
    with pytest.raises(RuntimeError):
        await _confirm(sessions, registry, actor, out.id)
    await _age_claim(sessions, out.id)
    fake.gate_error = HTTPException(403, "package_inactive")
    with pytest.raises(HTTPException) as gated:
        await _confirm(sessions, registry, actor, out.id)
    assert gated.value.detail == "package_inactive" and len(fake.executed) == 1
    assert (await _row(sessions, out.id))["status"] == "confirmed"
    fake.gate_error = None
    await sessions.q(
        "UPDATE console_proposals SET created_at = now() - interval '10 minutes',"
        " expires_at = now() - interval '1 second' WHERE id=%s",
        out.id,
    )
    with pytest.raises(ProposalError) as expired:
        await _confirm(sessions, registry, actor, out.id)
    assert expired.value.detail == "proposal_expired" and len(fake.executed) == 1
    row = await _row(sessions, out.id)
    assert row["status"] == "expired" and row["claim_token"] is None


async def test_changed_expected_version_is_stale(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    fake.version = "v2"
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, actor, out.id)
    assert exc.value.status_code == 409 and exc.value.detail == "proposal_stale"
    assert fake.executed == []
    assert (await _row(sessions, out.id))["status"] == "requested"


async def test_tampered_payload_is_stale(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)
    await sessions.q(
        'UPDATE console_proposals SET payload=\'{"title":"Other"}\' WHERE id=%s', out.id
    )
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, actor, out.id)
    assert exc.value.status_code == 409 and exc.value.detail == "proposal_stale"
    assert fake.executed == []


async def test_get_and_confirm_hide_other_users_and_tenants(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    stranger, _ = await _member_actor(sessions)
    _, registry = _world()
    out = await _create(sessions, registry, actor)
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        with pytest.raises(ProposalError) as exc:
            await registry.get(conn, stranger, UUID(tenant), out.id)
    assert exc.value.status_code == 404 and exc.value.detail == "proposal_not_found"
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, stranger, out.id)
    assert exc.value.status_code == 404
    other_tenant = sessions.tenants[1]
    async with tx(sessions.pool, other_tenant) as conn:
        with pytest.raises(ProposalError) as exc:
            await registry.get(conn, actor, UUID(other_tenant), out.id)
    assert exc.value.status_code == 404


async def test_cancel_and_cancel_thread_scope(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    thread, other_thread = uuid4(), uuid4()
    a = await _create(sessions, registry, actor, thread_id=thread)
    b = await _create(sessions, registry, actor, thread_id=thread)
    c = await _create(sessions, registry, actor, thread_id=other_thread)
    d = await _create(sessions, registry, actor)
    e = await _create(sessions, registry, actor, thread_id=thread)
    await _confirm(sessions, registry, actor, e.id)
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        assert await registry.cancel_thread(conn, actor, UUID(tenant), thread) == 2
    statuses = {
        p.id: (await _row(sessions, p.id))["status"] for p in (a, b, c, d, e)
    }  # fmt: skip
    assert statuses == {
        a.id: "cancelled", b.id: "cancelled", c.id: "requested", d.id: "requested",
        e.id: "executed",
    }  # fmt: skip
    async with tx(sessions.pool, tenant) as conn:
        cancelled = await registry.cancel(conn, actor, UUID(tenant), d.id)
    assert cancelled.status == "cancelled"
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, actor, a.id)
    assert exc.value.detail == "proposal_consumed"
    async with tx(sessions.pool, tenant) as conn:
        with pytest.raises(ProposalError) as exc:
            await registry.cancel(conn, actor, UUID(tenant), e.id)
    assert exc.value.status_code == 409 and exc.value.detail == "proposal_consumed"
    assert len(fake.executed) == 1
    audits = await sessions.q(
        "SELECT target_id FROM console_audit WHERE action='workspace.proposal.cancelled'"
    )
    assert sorted(r["target_id"] for r in audits) == sorted(str(p.id) for p in (a, b, d))


async def test_cancel_thread_records_past_expiry_rows_as_expired(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    _, registry = _world()
    thread = uuid4()
    stale = await _create(sessions, registry, actor, thread_id=thread)
    live = await _create(sessions, registry, actor, thread_id=thread)
    await sessions.q(
        "UPDATE console_proposals SET created_at = now() - interval '10 minutes',"
        " expires_at = now() - interval '1 second' WHERE id=%s",
        stale.id,
    )
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        assert await registry.cancel_thread(conn, actor, UUID(tenant), thread) == 1
    assert (await _row(sessions, stale.id))["status"] == "expired"
    assert (await _row(sessions, live.id))["status"] == "cancelled"


async def test_audit_rows_carry_the_proposal_id(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    _, registry = _world()
    out = await _create(sessions, registry, actor)
    await _confirm(sessions, registry, actor, out.id)
    rows = await sessions.q(
        "SELECT action, actor_id, client_id, details FROM console_audit"
        " WHERE target_id=%s ORDER BY created_at, action",
        str(out.id),
    )
    assert {r["action"] for r in rows} == {
        "workspace.proposal.created", "workspace.proposal.confirmed",
        "workspace.proposal.executed",
    }  # fmt: skip
    assert len(rows) == 3
    for r in rows:
        assert r["actor_id"] == actor.user_id
        assert str(r["client_id"]) == sessions.tenants[0]
        assert r["details"] == {"kind": "training_submit"}


# --------------------------------------------------------------------- HTTP: executors


@pytest.fixture
async def proposals_app(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[SessionWorld, FakeGmail]]:
    """SessionWorld app with HR and email routers, their services and the registry."""
    monkeypatch.setattr(crypto, "_key", lambda: b"\x07" * 32)
    sessions.app.include_router(build_hr_router())
    sessions.app.include_router(build_email_router())
    gmail = FakeGmail()
    http = httpx.AsyncClient(transport=httpx.MockTransport(gmail.handler))
    sessions.app.state.hr_service = HrService(sessions.pool, session_idle_seconds=1800)
    sessions.app.state.email_service = EmailService(
        sessions.pool,
        gmail=GmailClient("client-id", SecretStr("client-secret"), EMAIL_REDIRECT, http),
        model=ScriptedModel(),
        session_idle_seconds=1800,
    )
    sessions.app.state.proposal_registry = build_proposal_registry()
    try:
        yield sessions, gmail
    finally:
        await http.aclose()


PROPOSALS = "/console/v1/workspace/proposals"
THREADS = "/console/v1/workspace/conversations"


async def _propose(
    w: SessionWorld, browser: httpx.AsyncClient, kind: str, payload: dict[str, Any]
) -> httpx.Response:
    return await w.mutate(browser, PROPOSALS, {"kind": kind, "payload": payload})


async def _http_confirm(
    w: SessionWorld, browser: httpx.AsyncClient, proposal_id: str
) -> httpx.Response:
    return await w.mutate(browser, f"{PROPOSALS}/{proposal_id}/confirm")


async def _hr_member(w: SessionWorld, *, switch: bool = True) -> httpx.AsyncClient:
    tenant = w.tenants[0]
    await activate(w, tenant, "hr")
    await w.q(
        "UPDATE hr_agent_settings SET enabled=%s WHERE client_id=%s AND name='hr_assistant'",
        switch,
        tenant,
    )
    browser, me = await w.member(role="tenant_staff")
    user, employee, manager = me["user"]["id"], uuid4(), uuid4()
    await w.q(
        "INSERT INTO hr_employees(client_id,id,display_name,manager_id) VALUES"
        " (%s,%s,'Synthetic Manager',NULL),(%s,%s,'Synthetic Staff',%s)",
        tenant, manager, tenant, employee, manager,
    )  # fmt: skip
    await w.q(
        "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
        tenant, user, employee,
    )  # fmt: skip
    for action in ("training.read", "training.draft", "training.submit"):
        await w.q(
            "INSERT INTO hr_grants(client_id,user_id,action,scope,fields)"
            " VALUES (%s,%s,%s,'self','{}')",
            tenant, user, action,
        )  # fmt: skip
    return browser


async def test_training_submit_proposal_end_to_end(proposals_app) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    draft = await browser.post(
        "/console/v1/hr/training-requests",
        json={"title": "Forklift safety", "learning_objective": "Operate forklifts safely"},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )
    assert draft.status_code == 201, draft.text
    request_id = draft.json()["request"]["request_id"]
    created = await _propose(
        w, browser, "training_submit", {"action": "training_submit", "request_id": request_id}
    )
    assert created.status_code == 201, created.text
    out = created.json()
    assert out["status"] == "requested" and out["card"]["expected_version"] == "0"
    assert (await _row(w, UUID(out["id"])))["origin"] == "tab"
    assert [f["value"] for f in out["card"]["fields"]][0] == "Forklift safety"
    assert created.headers["Cache-Control"] == "no-store"
    seen = await browser.get(f"{PROPOSALS}/{out['id']}")
    assert seen.status_code == 200 and seen.json() == out
    done = await _http_confirm(w, browser, out["id"])
    assert done.status_code == 200, done.text
    receipt = done.json()["result"]
    assert done.json()["status"] == "executed"
    assert receipt["kind"] == "persisted_training"
    assert receipt["request"]["status"] == "submitted" and receipt["request"]["version"] == 1
    replay = await _http_confirm(w, browser, out["id"])
    assert replay.status_code == 200 and replay.json() == done.json()
    rows = await w.q("SELECT status FROM hr_training_requests WHERE id=%s", request_id)
    assert rows == [{"status": "submitted"}]


async def test_training_confirm_retried_after_registry_failure_replays_receipt(
    proposals_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    draft = await browser.post(
        "/console/v1/hr/training-requests",
        json={"title": "Forklift safety", "learning_objective": "Operate forklifts safely"},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )
    request_id = draft.json()["request"]["request_id"]
    created = await _propose(
        w, browser, "training_submit", {"action": "training_submit", "request_id": request_id}
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    failed = _fail_recording_executed_once(monkeypatch)
    with pytest.raises(RuntimeError, match="synthetic registry commit failure"):
        await _http_confirm(w, browser, pid)  # HR transition committed, registry write failed
    assert failed == ["executed"]
    rows = await w.q("SELECT status, version FROM hr_training_requests WHERE id=%s", request_id)
    assert rows == [{"status": "submitted", "version": 1}]
    assert (await _row(w, UUID(pid)))["status"] == "confirmed"
    busy = await _http_confirm(w, browser, pid)
    assert busy.status_code == 409 and busy.json()["detail"] == "proposal_consumed"
    await _age_claim(w, pid)
    retried = await _http_confirm(w, browser, pid)
    assert retried.status_code == 200, retried.text
    receipt = retried.json()["result"]
    assert retried.json()["status"] == "executed"
    assert receipt["duplicate"] is True and receipt["request"]["status"] == "submitted"
    assert receipt["request"]["version"] == 1
    replay = await _http_confirm(w, browser, pid)
    assert replay.status_code == 200 and replay.json() == retried.json()


async def test_training_submit_new_draft_proposal_creates_and_submits(proposals_app) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    created = await _propose(
        w,
        browser,
        "training_submit",
        {"title": "First aid", "learning_objective": "Certify in first aid"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["card"]["expected_version"] is None
    done = await _http_confirm(w, browser, created.json()["id"])
    assert done.status_code == 200, done.text
    request = done.json()["result"]["request"]
    assert request["status"] == "submitted" and request["content"]["title"] == "First aid"
    rows = await w.q("SELECT count(*) n FROM hr_training_requests WHERE client_id=%s", w.tenants[0])
    assert rows[0]["n"] == 1


async def test_tab_training_submit_needs_no_hr_assistant_switch(proposals_app) -> None:
    """The switch governs the AI assistant; a human confirming a tab card is not gated by it."""
    w, _ = proposals_app
    browser = await _hr_member(w, switch=False)
    draft = await browser.post(
        "/console/v1/hr/training-requests",
        json={"title": "First aid", "learning_objective": "Certify in first aid"},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )
    assert draft.status_code == 201, draft.text
    request_id = draft.json()["request"]["request_id"]
    created = await _propose(
        w, browser, "training_submit", {"action": "training_submit", "request_id": request_id}
    )
    assert created.status_code == 201, created.text
    assert (await _row(w, UUID(created.json()["id"])))["origin"] == "tab"
    done = await _http_confirm(w, browser, created.json()["id"])
    assert done.status_code == 200, done.text
    assert done.json()["result"]["request"]["status"] == "submitted"
    rows = await w.q("SELECT status FROM hr_training_requests WHERE id=%s", request_id)
    assert rows == [{"status": "submitted"}]


def _hr_assistant(w: SessionWorld, action: dict[str, Any]) -> httpx.AsyncClient:
    """Install an HrAssistant whose scripted model plans the given action."""

    def provider(_: httpx.Request) -> httpx.Response:
        content = json.dumps({"action": action})
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    w.app.state.hr_assistant = HrAssistant(
        w.app.state.hr_service, GroqClient("synthetic-key", "synthetic-model", http)
    )
    return http


async def _ask_hr(w: SessionWorld, browser: httpx.AsyncClient) -> httpx.Response:
    return await browser.post(
        "/console/v1/hr/assistant",
        json={"message": "Submit my forklift training"},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )


async def test_hr_assistant_submit_returns_proposal_confirmed_through_workspace(
    proposals_app,
) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    draft = await browser.post(
        "/console/v1/hr/training-requests",
        json={"title": "Forklift safety", "learning_objective": "Operate forklifts safely"},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )
    request_id = draft.json()["request"]["request_id"]
    http = _hr_assistant(
        w,
        {
            "action": "training.transition",
            "request_id": request_id,
            "command": {"action": "submit", "expected_version": 0},
        },
    )
    try:
        asked = await _ask_hr(w, browser)
    finally:
        await http.aclose()
    assert asked.status_code == 201, asked.text
    out = asked.json()
    assert out["kind"] == "training_submit" and out["status"] == "requested"
    assert out["card"]["expected_version"] == "0"
    assert (await _row(w, UUID(out["id"])))["origin"] == "assistant"
    rows = await w.q("SELECT status FROM hr_training_requests WHERE id=%s", request_id)
    assert rows == [{"status": "draft"}]  # nothing is applied before confirmation
    done = await _http_confirm(w, browser, out["id"])
    assert done.status_code == 200, done.text
    assert done.json()["result"]["request"]["status"] == "submitted"
    rows = await w.q("SELECT status FROM hr_training_requests WHERE id=%s", request_id)
    assert rows == [{"status": "submitted"}]


async def test_hr_assistant_submit_replay_returns_the_same_proposal(proposals_app) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    draft = await browser.post(
        "/console/v1/hr/training-requests",
        json={"title": "Forklift safety", "learning_objective": "Operate forklifts safely"},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )
    request_id = draft.json()["request"]["request_id"]
    http = _hr_assistant(
        w,
        {
            "action": "training.transition",
            "request_id": request_id,
            "command": {"action": "submit", "expected_version": 0},
        },
    )

    async def ask(key: UUID) -> httpx.Response:
        return await browser.post(
            "/console/v1/hr/assistant",
            json={"message": "Submit my forklift training"},
            headers={**w.headers(browser), "Idempotency-Key": str(key)},
        )

    try:
        key = uuid4()
        first = await ask(key)
        second = await ask(key)
        assert first.status_code == 201, first.text
        assert second.status_code == 200 and second.json() == first.json()
        assert (await w.q("SELECT count(*) n FROM console_proposals"))[0]["n"] == 1
        done = await _http_confirm(w, browser, first.json()["id"])
        assert done.status_code == 200, done.text
        replay = await ask(key)
        assert replay.json()["id"] == first.json()["id"] and replay.json()["status"] == "executed"
        assert (await w.q("SELECT count(*) n FROM console_proposals"))[0]["n"] == 1
    finally:
        await http.aclose()


async def _submit_ready_draft(w: SessionWorld, browser: httpx.AsyncClient) -> httpx.AsyncClient:
    draft = await browser.post(
        "/console/v1/hr/training-requests",
        json={"title": "Forklift safety", "learning_objective": "Operate forklifts safely"},
        headers={**w.headers(browser), "Idempotency-Key": str(uuid4())},
    )
    return _hr_assistant(
        w,
        {
            "action": "training.transition",
            "request_id": draft.json()["request"]["request_id"],
            "command": {"action": "submit", "expected_version": 0},
        },
    )


async def test_hr_assistant_concurrent_same_key_creates_one_proposal(proposals_app) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    http = await _submit_ready_draft(w, browser)
    key = str(uuid4())

    async def ask() -> httpx.Response:
        return await browser.post(
            "/console/v1/hr/assistant",
            json={"message": "Submit my forklift training"},
            headers={**w.headers(browser), "Idempotency-Key": key},
        )

    try:
        replies = await asyncio.gather(*[ask() for _ in range(4)])
    finally:
        await http.aclose()
    assert all(r.status_code in {200, 201} for r in replies), [r.text for r in replies]
    assert len({r.json()["id"] for r in replies}) == 1
    assert len({r.json()["status"] for r in replies}) == 1
    assert (await w.q("SELECT count(*) n FROM console_proposals"))[0]["n"] == 1
    audits = await w.q(
        "SELECT count(*) n FROM console_audit WHERE action='workspace.proposal.created'"
    )
    assert audits[0]["n"] == 1


async def test_hr_assistant_different_keys_create_distinct_proposals(proposals_app) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w)
    http = await _submit_ready_draft(w, browser)

    async def ask() -> httpx.Response:
        return await _ask_hr(w, browser)

    try:
        first, second = await asyncio.gather(ask(), ask())
    finally:
        await http.aclose()
    assert first.status_code == second.status_code == 201, (first.text, second.text)
    assert first.json()["id"] != second.json()["id"]
    assert (await w.q("SELECT count(*) n FROM console_proposals"))[0]["n"] == 2


async def test_hr_assistant_submit_refused_when_switch_is_off(proposals_app) -> None:
    w, _ = proposals_app
    browser = await _hr_member(w, switch=False)
    http = _hr_assistant(
        w,
        {
            "action": "training.transition",
            "request_id": str(uuid4()),
            "command": {"action": "submit", "expected_version": 0},
        },
    )
    try:
        asked = await _ask_hr(w, browser)
    finally:
        await http.aclose()
    assert asked.status_code == 409 and asked.json()["detail"] == "agent_disabled"
    assert (await w.q("SELECT count(*) n FROM console_proposals"))[0]["n"] == 0


async def test_kind_without_its_package_is_refused_before_any_row(proposals_app) -> None:
    w, _ = proposals_app
    browser, _ = await w.member(role="tenant_staff")
    payload = {"title": "First aid", "learning_objective": "Certify"}
    created = await _propose(w, browser, "training_submit", payload)
    assert created.status_code == 403 and created.json()["detail"] == "package_inactive"
    exported = await _propose(w, browser, "email_export", {"draft_id": str(uuid4())})
    assert exported.status_code == 403 and exported.json()["detail"] == "package_inactive"
    assert (await w.q("SELECT count(*) n FROM console_proposals"))[0]["n"] == 0
    reader, _ = await w.member(role="read_only")
    denied = await _propose(w, reader, "training_submit", payload)
    assert denied.status_code == 403


async def _held_approval(w: SessionWorld) -> tuple[int, str, int]:
    await activate(w, w.tenants[0], "customer_support")
    env, conversation_id = await seed(w)
    approval = await held(w, env)
    rows = await w.q("SELECT inbox_version FROM conversations WHERE id=%s", conversation_id)
    return approval, conversation_id, rows[0]["inbox_version"]


def _decision(approval: int, conversation_id: str, version: int) -> dict[str, Any]:
    return {
        "action": "inbox_decision",
        "approval_id": approval,
        "conversation_id": conversation_id,
        "decision": "approve",
        "expected_version": version,
    }


async def test_inbox_decision_proposal_end_to_end(proposals_app) -> None:
    w, _ = proposals_app
    approval, conversation_id, version = await _held_approval(w)
    browser, _ = await w.member(role="tenant_staff")
    created = await _propose(
        w, browser, "inbox_decision", _decision(approval, conversation_id, version)
    )
    assert created.status_code == 201, created.text
    assert created.json()["card"]["expected_version"] == str(version)
    done = await _http_confirm(w, browser, created.json()["id"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed"
    assert done.json()["result"]["outcome"] == "applied"
    rows = await w.q("SELECT status FROM approvals WHERE id=%s", approval)
    assert rows == [{"status": "approved"}]
    replay = await _http_confirm(w, browser, created.json()["id"])
    assert replay.status_code == 200 and replay.json() == done.json()
    decided = await w.q(
        "SELECT count(*) n FROM console_audit WHERE action='inbox.reply_decided' AND target_id=%s",
        str(approval),
    )
    assert decided[0]["n"] == 1


async def test_inbox_decision_after_conversation_moved_is_stale(proposals_app) -> None:
    w, _ = proposals_app
    approval, conversation_id, version = await _held_approval(w)
    browser, _ = await w.member(role="tenant_staff")
    created = await _propose(
        w, browser, "inbox_decision", _decision(approval, conversation_id, version)
    )
    assert created.status_code == 201, created.text
    await w.q("UPDATE conversations SET inbox_version=inbox_version+1 WHERE id=%s", conversation_id)
    stale = await _http_confirm(w, browser, created.json()["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale"
    rows = await w.q("SELECT status FROM approvals WHERE id=%s", approval)
    assert rows == [{"status": "pending"}]
    row = await _row(w, UUID(created.json()["id"]))
    assert row["status"] == "requested" and row["result"] is None
    mismatch = await _propose(
        w, browser, "inbox_decision", _decision(approval, conversation_id, version)
    )
    assert mismatch.status_code == 409 and mismatch.json()["detail"] == "proposal_stale"


async def test_expired_confirm_over_http_persists_expired(proposals_app) -> None:
    w, _ = proposals_app
    approval, conversation_id, version = await _held_approval(w)
    browser, _ = await w.member(role="tenant_staff")
    created = await _propose(
        w, browser, "inbox_decision", _decision(approval, conversation_id, version)
    )
    await w.q(
        "UPDATE console_proposals SET created_at = now() - interval '10 minutes',"
        " expires_at = now() - interval '1 second' WHERE id=%s",
        created.json()["id"],
    )
    expired = await _http_confirm(w, browser, created.json()["id"])
    assert expired.status_code == 409 and expired.json()["detail"] == "proposal_expired"
    assert (await _row(w, UUID(created.json()["id"])))["status"] == "expired"
    cancelled = await w.mutate(browser, f"{PROPOSALS}/{created.json()['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "expired"


async def test_email_export_proposal_end_to_end(proposals_app) -> None:
    w, gmail = proposals_app
    tenant = w.tenants[0]
    await activate(w, tenant, "staff_email")
    await w.q("UPDATE email_settings SET enabled=true WHERE client_id=%s", tenant)
    browser, _ = await w.member(role="tenant_staff")

    async def call(method: str, path: str, body: Any = None) -> httpx.Response:
        headers = {**w.headers(browser), "Idempotency-Key": str(uuid4())}
        return await browser.request(method, path, json=body, headers=headers)

    started = await call("POST", "/console/v1/email/mailbox/connect")
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    done = await browser.get(f"/console/v1/email/oauth/callback?code=abc&state={state}")
    assert done.status_code == 303 and "error" not in done.headers["location"], done.text
    assert (await call("POST", "/console/v1/email/sync")).status_code == 200
    drafted = await call("POST", "/console/v1/email/threads/t1/draft", {"instructions": "Hi"})
    assert drafted.status_code == 201, drafted.text
    draft_id = drafted.json()["id"]
    created = await _propose(w, browser, "email_export", {"draft_id": draft_id})
    assert created.status_code == 201, created.text
    card = created.json()["card"]
    assert card["title"] == "Save email to Gmail drafts for 1 person"
    assert card["summary"] == "1 in To. Nothing is sent."
    assert {f["label"]: f["value"] for f in card["fields"]} == {
        "Subject": "(shown on open)",
        "Draft": draft_id,
    }
    exported = await _http_confirm(w, browser, created.json()["id"])
    assert exported.status_code == 200, exported.text
    assert exported.json()["result"]["gmail_draft_id"] == "gmail-draft-1"
    assert exported.json()["result"]["status"] == "exported"
    assert set(exported.json()["result"]) == {"draft_id", "status", "gmail_draft_id"}
    assert sum(1 for m, p, _ in gmail.requests if m == "POST" and p.endswith("/drafts")) == 1
    replay = await _http_confirm(w, browser, created.json()["id"])
    assert replay.status_code == 200 and replay.json() == exported.json()
    assert sum(1 for m, p, _ in gmail.requests if m == "POST" and p.endswith("/drafts")) == 1


# ------------------------------------------------------------------ email_send proposals

SEND_SUBJECT = "Synthetic roadmap Q7"
SEND_BODY = "Synthetic body line Z9Q, nothing to fill in."


class CleanDraftModel(ScriptedModel):
    """Drafts with no [STAFF: ...] placeholder, so they can be sent."""

    async def json_completion(
        self, schema: type[Any], *, system: str, user: str, timeout: float, max_tokens: int = 1500
    ) -> Any:
        if schema is DraftProposal:
            self.calls.append((schema.__name__, system, user))
            return DraftProposal(subject=SEND_SUBJECT, body=SEND_BODY)
        return await super().json_completion(
            schema, system=system, user=user, timeout=timeout, max_tokens=max_tokens
        )


class OnePlan:
    """Planner model double: always proposes the same action."""

    model = "scripted"

    def __init__(self, proposal: dict[str, Any]) -> None:
        self.proposal = proposal

    async def json_completion(
        self, schema: type[Any], *, system: str, user: str, timeout: float, max_tokens: int = 1500
    ) -> Any:
        return schema.model_validate({"proposal": self.proposal})


async def _email_ready(w: SessionWorld) -> httpx.AsyncClient:
    tenant = w.tenants[0]
    await activate(w, tenant, "staff_email")
    await w.q("UPDATE email_settings SET enabled=true WHERE client_id=%s", tenant)
    browser, _ = await w.member(role="tenant_staff")
    started = await _email_call(w, browser, "POST", "/console/v1/email/mailbox/connect")
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    done = await browser.get(f"/console/v1/email/oauth/callback?code=abc&state={state}")
    assert done.status_code == 303 and "error" not in done.headers["location"], done.text
    return browser


async def _email_call(
    w: SessionWorld, browser: httpx.AsyncClient, method: str, path: str, body: Any = None
) -> httpx.Response:
    headers = {**w.headers(browser), "Idempotency-Key": str(uuid4())}
    return await browser.request(method, path, json=body, headers=headers)


async def test_send_card_goes_stale_after_draft_edit(proposals_app) -> None:
    """Review Focus 2: an edit after the card was made sends nothing until a new card."""
    w, gmail = proposals_app
    browser = await _email_ready(w)
    assert (await _email_call(w, browser, "POST", "/console/v1/email/sync")).status_code == 200
    drafted = await _email_call(
        w, browser, "POST", "/console/v1/email/threads/t1/draft", {"instructions": "Hi"}
    )
    assert drafted.status_code == 201, drafted.text
    draft = DraftOut.model_validate(drafted.json())
    version = draft_version(draft)
    created = await _propose(
        w, browser, "email_send", {"draft_id": str(draft.id), "version": version}
    )
    assert created.status_code == 201, created.text
    card = created.json()["card"]
    assert card["kind"] == "email_send" and card["expected_version"] == version
    assert card["title"] == "Send reply to 1 person"
    assert {f["label"]: f["value"] for f in card["fields"]} == {
        "Subject": "(shown on open)",
        "Draft": str(draft.id),
    }
    assert "aina@example.test" not in json.dumps(card)

    edited = await _email_call(
        w,
        browser,
        "PUT",
        f"/console/v1/email/drafts/{draft.id}",
        {"to": ["aina@example.test"], "subject": draft.subject, "body": "Hi Aina, it ships today."},
    )
    assert edited.status_code == 200, edited.text
    stale = await _http_confirm(w, browser, created.json()["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "draft_changed"
    assert gmail.sends == []
    assert (await _row(w, UUID(created.json()["id"])))["status"] == "requested"
    again = await _propose(
        w, browser, "email_send", {"draft_id": str(draft.id), "version": version}
    )
    assert again.status_code == 409 and again.json()["detail"] == "draft_changed"

    fresh_version = draft_version(DraftOut.model_validate(edited.json()))
    fresh = await _propose(
        w, browser, "email_send", {"draft_id": str(draft.id), "version": fresh_version}
    )
    assert fresh.status_code == 201, fresh.text
    sent = await _http_confirm(w, browser, fresh.json()["id"])
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "executed" and sent.json()["result"]["outcome"] == "sent"
    assert len(gmail.sends) == 1


async def test_send_proposal_stores_no_addresses_or_body(proposals_app) -> None:
    w, gmail = proposals_app
    w.app.state.email_service.model = CleanDraftModel()
    outside, copied = "outside.person@gmail.com", "copy.person@example.test"
    w.app.state.workspace_planner = WorkspacePlanner(
        OnePlan(
            {
                "action": "email_compose",
                "recipient_address": outside,
                "cc": [copied],
                "subject": "Plan",
                "instructions": "Share the plan",
                "send": True,
            }
        )
    )
    browser = await _email_ready(w)
    created = await w.mutate(browser, THREADS, {"request_key": str(uuid4())})
    assert created.status_code == 201, created.text
    turn = await w.mutate(
        browser,
        f"{THREADS}/{created.json()['id']}/turns",
        {
            "request_key": str(uuid4()),
            "body": f"Email {outside} and cc {copied} about the plan, then send it",
            "page_context": {"tab": "email"},
        },
    )
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["result"]["proposal"]
    assert proposal["kind"] == "email_send"
    assert proposal["card"]["title"] == "Send email to 2 people (1 outside your company)"
    done = await _http_confirm(w, browser, proposal["id"])
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed"
    assert set(done.json()["result"]) == {
        "draft_id", "outcome", "sent_at", "gmail_message_id", "remaining_this_hour",
    }  # fmt: skip
    assert len(gmail.sends) == 1

    rows = await w.q("SELECT kind, payload FROM console_proposals")
    assert [r["kind"] for r in rows] == ["email_send"]
    assert set(rows[0]["payload"]) == {"draft_id", "version"}
    await _assert_not_stored(w, outside, copied, "staff@example.test", SEND_SUBJECT, "Z9Q")


async def _assert_not_stored(w: SessionWorld, *needles: str) -> None:
    """No needle in any proposal, turn or console audit row (all readable tenant-wide)."""
    stored = [
        *(
            r["t"]
            for r in await w.q(
                "SELECT concat_ws(' ', payload::text, card::text, result::text) t"
                " FROM console_proposals"
            )
        ),
        *(
            r["t"]
            for r in await w.q(
                "SELECT concat_ws(' ', body, result::text) t FROM console_workspace_turns"
            )
        ),
        *(r["t"] for r in await w.q("SELECT details::text t FROM console_audit")),
    ]
    assert stored
    for needle in needles:
        assert all(needle.lower() not in text.lower() for text in stored), needle


async def test_export_proposal_stores_no_addresses_or_body(proposals_app) -> None:
    """A reply the user did not ask to send gets an export card, also counts only."""
    w, gmail = proposals_app
    w.app.state.email_service.model = CleanDraftModel()
    copied = "copy.person@example.test"
    w.app.state.workspace_planner = WorkspacePlanner(
        OnePlan({"action": "email_reply", "thread_id": "t1", "instructions": "Hi", "cc": [copied]})
    )
    browser = await _email_ready(w)
    assert (await _email_call(w, browser, "POST", "/console/v1/email/sync")).status_code == 200
    created = await w.mutate(browser, THREADS, {"request_key": str(uuid4())})
    assert created.status_code == 201, created.text
    turn = await w.mutate(
        browser,
        f"{THREADS}/{created.json()['id']}/turns",
        {
            "request_key": str(uuid4()),
            "body": f"Reply to Aina's email and copy {copied}",
            "page_context": {"tab": "email"},
        },
    )
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["result"]["proposal"]
    assert proposal["kind"] == "email_export"
    assert proposal["card"]["title"] == "Save email to Gmail drafts for 2 people"
    assert proposal["card"]["summary"] == "1 in To, 1 in CC. Nothing is sent."
    done = await _http_confirm(w, browser, proposal["id"])
    assert done.status_code == 200, done.text
    assert done.json()["result"]["status"] == "exported"
    assert sum(1 for m, p, _ in gmail.requests if m == "POST" and p.endswith("/drafts")) == 1
    rows = await w.q("SELECT kind, payload FROM console_proposals")
    assert [(r["kind"], set(r["payload"])) for r in rows] == [("email_export", {"draft_id"})]
    await _assert_not_stored(
        w, "aina@example.test", copied, "staff@example.test", SEND_SUBJECT, "Z9Q", "MGL-TEST-005"
    )


async def _connected_member(w: SessionWorld) -> httpx.AsyncClient:
    """Another tenant_staff member with their own connected mailbox (the tenant is ready)."""
    browser, _ = await w.member(role="tenant_staff")
    started = await _email_call(w, browser, "POST", "/console/v1/email/mailbox/connect")
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
    done = await browser.get(f"/console/v1/email/oauth/callback?code=abc&state={state}")
    assert done.status_code == 303 and "error" not in done.headers["location"], done.text
    return browser


async def _new_draft(w: SessionWorld, browser: httpx.AsyncClient) -> DraftOut:
    drafted = await _email_call(
        w, browser, "POST", "/console/v1/email/threads/t1/draft", {"instructions": "Hi"}
    )
    assert drafted.status_code == 201, drafted.text
    return DraftOut.model_validate(drafted.json())


async def test_send_card_for_another_members_draft_is_refused(proposals_app) -> None:
    """A send card names only the caller's own drafts, at creation and again at confirm."""
    w, gmail = proposals_app
    w.app.state.email_service.model = CleanDraftModel()
    owner = await _email_ready(w)
    assert (await _email_call(w, owner, "POST", "/console/v1/email/sync")).status_code == 200
    theirs = await _new_draft(w, owner)
    other = await _connected_member(w)
    assert (await _email_call(w, other, "POST", "/console/v1/email/sync")).status_code == 200
    payload = {"draft_id": str(theirs.id), "version": draft_version(theirs)}
    refused = await _propose(w, other, "email_send", payload)
    assert refused.status_code == 404 and refused.json()["detail"] == "resource_unavailable"
    # A card made for the member's own draft, then pointed at the other draft (hash kept
    # valid), is refused at confirm as well.
    mine = await _new_draft(w, other)
    created = await _propose(
        w, other, "email_send", {"draft_id": str(mine.id), "version": draft_version(mine)}
    )
    assert created.status_code == 201, created.text
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    await w.q(
        "UPDATE console_proposals SET payload=%s, payload_sha256=%s, expected_version=%s "
        "WHERE id=%s",
        json.dumps(payload),
        hashlib.sha256(canonical.encode()).hexdigest(),
        payload["version"],
        created.json()["id"],
    )
    confirmed = await _http_confirm(w, other, created.json()["id"])
    assert confirmed.status_code == 404 and confirmed.json()["detail"] == "resource_unavailable"
    assert gmail.sends == []
    statuses = await w.q("SELECT status FROM email_drafts ORDER BY created_at")
    assert [row["status"] for row in statuses] == ["draft", "draft"]


async def test_export_card_goes_stale_after_a_cc_edit(proposals_app) -> None:
    """The export card binds the whole draft, CC and BCC included, as a send card does."""
    w, gmail = proposals_app
    browser = await _email_ready(w)
    assert (await _email_call(w, browser, "POST", "/console/v1/email/sync")).status_code == 200
    draft = await _new_draft(w, browser)
    created = await _propose(w, browser, "email_export", {"draft_id": str(draft.id)})
    assert created.status_code == 201, created.text
    assert created.json()["card"]["expected_version"] == draft_version(draft)
    edited = await _email_call(
        w,
        browser,
        "PUT",
        f"/console/v1/email/drafts/{draft.id}",
        {
            "to": draft.to,
            "cc": ["copy.person@example.test"],
            "subject": draft.subject,
            "body": draft.body,
        },
    )
    assert edited.status_code == 200, edited.text
    stale = await _http_confirm(w, browser, created.json()["id"])
    assert stale.status_code == 409 and stale.json()["detail"] == "proposal_stale", stale.text
    assert not any(m == "POST" and p.endswith("/drafts") for m, p, _ in gmail.requests)


async def test_email_cards_need_a_connected_mailbox(proposals_app) -> None:
    """A disconnected mailbox is refused when the card is made, not at Confirm."""
    w, gmail = proposals_app
    browser = await _email_ready(w)
    assert (await _email_call(w, browser, "POST", "/console/v1/email/sync")).status_code == 200
    draft = await _new_draft(w, browser)
    disconnected = await _email_call(w, browser, "POST", "/console/v1/email/mailbox/disconnect")
    assert disconnected.status_code == 200, disconnected.text
    export = await _propose(w, browser, "email_export", {"draft_id": str(draft.id)})
    assert export.status_code == 409, export.text
    assert export.json()["detail"] == "mailbox_not_connected"
    send = await _propose(
        w, browser, "email_send", {"draft_id": str(draft.id), "version": draft_version(draft)}
    )
    assert send.status_code == 409, send.text
    assert send.json()["detail"] == "mailbox_not_connected"
    assert await w.q("SELECT id FROM console_proposals") == []
    assert gmail.sends == []
