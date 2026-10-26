"""Schema contract for the workspace assistant: proposals, directory view, relaxed turns."""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.console.contracts import ConsoleActor, MembershipOut, Role
from csa.core.tenancy import tx
from csa.workspace.contracts import CardField, ProposalKind
from csa.workspace.registry import CardContent, ProposalError, ProposalRegistry
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from starlette.requests import Request

from tests.conftest import requires_docker
from tests.test_console_sessions import MEMBER_PASSWORD, SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

pytestmark = requires_docker

INSERT_PROPOSAL = (
    "INSERT INTO console_proposals (client_id,user_id,login_session_id,kind,payload,"
    "payload_sha256,status,expires_at) "
    "VALUES (%s,%s,%s,%s,'{}',%s,'requested',now()+interval '1 minute')"
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
    with pytest.raises(Exception, match="expires_at|violates check"):
        await sessions.q(
            "INSERT INTO console_proposals (client_id,user_id,login_session_id,kind,payload,"
            "payload_sha256,status,expires_at) VALUES (%s,%s,%s,'email_export','{}',%s,"
            "'requested',now()+interval '16 minutes')",
            t,
            sessions.admin_id,
            uuid4(),
            "0" * 64,
        )
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

    def __init__(self) -> None:
        self.version: str | None = "v1"
        self.executed: list[dict[str, Any]] = []

    async def card(
        self, conn: AsyncConnection[Any], actor: ConsoleActor, client_id: UUID, payload: dict
    ) -> tuple[CardContent, str | None]:
        content = CardContent(
            title="Submit training request",
            summary=f"Submit {payload['title']}",
            fields=[CardField(label="Title", value=str(payload["title"]))],
        )
        return content, self.version

    async def execute(
        self,
        conn: AsyncConnection[Any],
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict,
    ) -> dict:
        self.executed.append(payload)
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
) -> Any:
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        return await registry.create(
            conn, actor, UUID(tenant), "training_submit", {"title": title}, thread_id=thread_id
        )


async def _confirm(sessions: SessionWorld, registry: ProposalRegistry, actor, pid) -> Any:
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        return await registry.confirm(conn, _request(), actor, UUID(tenant), pid)


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
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        with pytest.raises(ProposalError) as exc:
            await registry.confirm(conn, _request(), actor, UUID(tenant), out.id)
    assert exc.value.status_code == 409 and exc.value.detail == "proposal_expired"
    assert (await _row(sessions, out.id))["status"] == "expired"
    assert fake.executed == []
    with pytest.raises(ProposalError) as again:
        await _confirm(sessions, registry, actor, out.id)
    assert again.value.status_code == 409 and again.value.detail == "proposal_consumed"


async def test_expiry_persists_even_when_the_confirm_transaction_rolls_back(
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
        await _confirm(sessions, registry, actor, out.id)  # error escapes: tx rolls back
    assert exc.value.detail == "proposal_expired"
    assert (await _row(sessions, out.id))["status"] == "requested"
    tenant = sessions.tenants[0]
    async with tx(sessions.pool, tenant) as conn:
        seen = await registry.get(conn, actor, UUID(tenant), out.id)
    assert seen.status == "expired"
    assert (await _row(sessions, out.id))["status"] == "expired"
    assert fake.executed == []


async def test_confirm_executes_once_and_second_confirm_is_consumed(
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
    with pytest.raises(ProposalError) as exc:
        await _confirm(sessions, registry, actor, out.id)
    assert exc.value.status_code == 409 and exc.value.detail == "proposal_consumed"
    assert len(fake.executed) == 1


async def test_executor_failure_rolls_back_to_requested(sessions: SessionWorld) -> None:
    actor, _ = await _member_actor(sessions)
    fake, registry = _world()
    out = await _create(sessions, registry, actor)

    async def boom(*_: Any) -> dict:
        raise RuntimeError("synthetic executor failure")

    fake.execute = boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="synthetic executor failure"):
        await _confirm(sessions, registry, actor, out.id)
    row = await _row(sessions, out.id)
    assert row["status"] == "requested" and row["result"] is None


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
