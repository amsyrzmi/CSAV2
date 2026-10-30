"""Authenticated workspace assistant behavior on disposable synthetic tenants."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import httpx
import psycopg
import pytest
from csa.core import db
from csa.core.tenancy import tx

from tests.conftest import requires_docker
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld
from tests.test_workspace_planner import (  # noqa: F401 - fixture import
    PROPOSALS,
    _approves_team,
    _mail_ready,
    _sync,
    _team_lead,
    _thread,
    _turn,
    _turn_rows,
    planned,
)
from tests.test_workspace_registry import CleanDraftModel, proposals_app  # noqa: F401

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]


def workspace_path(tenant: str | None = None, suffix: str = "") -> str:
    query = f"?client_id={tenant}" if tenant else ""
    return f"/console/v1/workspace/conversations{suffix}{query}"


async def test_workspace_session_tenant_owner_and_role_boundaries(
    sessions: SessionWorld,
) -> None:
    world = sessions
    await world.q("TRUNCATE console_workspace_turns,console_workspace_threads CASCADE")
    tenant_member, _ = await world.member()
    other_tenant_member, _ = await world.member(tenant=world.tenants[1])
    reader, _ = await world.member(role="read_only")
    key = str(uuid4())
    created = await world.mutate(
        world.admin,
        workspace_path(world.tenants[0]),
        {"request_key": key},
    )
    assert created.status_code == 201, created.text
    repeated = await world.mutate(
        world.admin,
        workspace_path(world.tenants[0]),
        {"request_key": key},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == created.json()["id"]
    assert repeated.json()["duplicate"] is True
    thread_id = created.json()["id"]

    assert (await world.admin.get(workspace_path())).status_code == 400
    assert (
        await world.admin.get(workspace_path(world.tenants[1], f"/{thread_id}/turns"))
    ).status_code == 404
    assert (await world.admin.get(workspace_path(world.tenants[1]))).status_code == 200
    assert (await world.admin.get(workspace_path(world.tenants[1]))).json()["items"] == []
    assert (await world.admin.get(workspace_path(world.tenants[0], f"/{thread_id}/turns"))).json()[
        "items"
    ] == []
    assert (
        await tenant_member.get(workspace_path(suffix=f"/{thread_id}/turns"))
    ).status_code == 404
    assert (
        await other_tenant_member.get(workspace_path(suffix=f"/{thread_id}/turns"))
    ).status_code == 404
    assert (await reader.get(workspace_path(suffix=f"/{thread_id}/turns"))).status_code == 403
    assert (await reader.get(workspace_path())).status_code == 403
    assert (
        await world.mutate(
            reader,
            workspace_path(world.tenants[0]),
            {"request_key": str(uuid4())},
        )
    ).status_code == 403
    assert (
        await other_tenant_member.get(workspace_path(world.tenants[0], f"/{thread_id}/turns"))
    ).status_code == 403
    assert (
        await world.admin.post(
            workspace_path(world.tenants[0]),
            json={"request_key": str(uuid4())},
        )
    ).status_code == 403
    spoofed = await world.admin.get(
        workspace_path(world.tenants[0]),
        headers={"X-Actor-Role": "tenant_admin"},
    )
    assert spoofed.status_code in {400, 403}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app), base_url=ORIGIN
    ) as machine:
        assert (
            await machine.get(
                workspace_path(world.tenants[0]),
                headers={
                    "Authorization": f"Bearer {MACHINE_TOKEN}",
                    "X-Client-Id": world.tenants[0],
                },
            )
        ).status_code == 403
    anonymous = await world.browser()
    anonymous.cookies.clear()
    assert (await anonymous.get(workspace_path(world.tenants[0]))).status_code == 401


async def test_workspace_order_read_is_local_bounded_and_version_checked(
    sessions: SessionWorld,
) -> None:
    world = sessions
    await world.q("TRUNCATE console_workspace_turns,console_workspace_threads CASCADE")
    tenant = world.tenants[0]
    order_id = f"synthetic-workspace-{uuid4()}"
    async with await psycopg.AsyncConnection.connect(world.owner_dsn, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO orders (id,client_id,status,total,tracking_no,carrier,ship_to) "
            "VALUES (%s,%s,'shipped',12.34,'SYNTHETIC-TRACK','Synthetic Carrier',"
            '\'{"private_address":"not-for-workspace"}\')',
            (order_id, tenant),
        )
    thread = await world.mutate(
        world.admin,
        workspace_path(tenant),
        {"request_key": str(uuid4())},
    )
    assert thread.status_code == 201, thread.text
    thread_id = thread.json()["id"]
    request_key = str(uuid4())
    turn_path = workspace_path(tenant, f"/{thread_id}/turns")
    payload = {
        "request_key": request_key,
        "body": "Read this local order",
        "page_hint": {
            "page": "business_order",
            "entity": {"kind": "local_order", "order_id": order_id},
        },
    }
    first = await world.mutate(world.admin, turn_path, payload)
    assert first.status_code == 201, first.text
    result = first.json()["result"]
    assert result["status"] == "completed"
    assert result["action"] == "local_order_read"
    assert result["source"] == "local_records"
    assert result["marketplace_sync"] == "unavailable"
    assert result["order"]["resource"]["order_id"] == order_id
    assert result["order"]["tracking_no"] == "SYNTHETIC-TRACK"
    assert "private_address" not in first.text
    repeated = await world.mutate(world.admin, turn_path, payload)
    assert repeated.status_code == 200
    assert repeated.json()["id"] == first.json()["id"]
    assert repeated.json()["duplicate"] is True
    history = await world.admin.get(turn_path)
    assert history.status_code == 200, history.text
    assert history.headers["Cache-Control"] == "no-store"
    assert history.json()["items"] == [first.json()]
    assert history.json()["next_cursor"] is None

    changed = {**payload, "body": "A different request"}
    assert (await world.mutate(world.admin, turn_path, changed)).status_code == 409
    async with await psycopg.AsyncConnection.connect(world.owner_dsn, autocommit=True) as conn:
        await conn.execute(
            "UPDATE orders SET updated_at=%s WHERE id=%s AND client_id=%s",
            (datetime.now(UTC), order_id, tenant),
        )
    stale = await world.mutate(world.admin, turn_path, payload)
    assert stale.status_code == 409

    missing = {
        **payload,
        "request_key": str(uuid4()),
        "page_hint": {
            "page": "business_order",
            "entity": {"kind": "local_order", "order_id": f"missing-{uuid4()}"},
        },
    }
    denied = await world.mutate(world.admin, turn_path, missing)
    assert denied.status_code == 201
    assert denied.json()["result"] == {
        "status": "denied",
        "code": "resource_unavailable",
    }
    foreign_order = f"synthetic-foreign-workspace-{uuid4()}"
    async with await psycopg.AsyncConnection.connect(world.owner_dsn, autocommit=True) as conn:
        await conn.execute(
            "INSERT INTO orders (id,client_id,status,total) VALUES (%s,%s,'shipped',12.34)",
            (foreign_order, world.tenants[1]),
        )
    foreign = {
        **payload,
        "request_key": str(uuid4()),
        "page_hint": {
            "page": "business_order",
            "entity": {"kind": "local_order", "order_id": foreign_order},
        },
    }
    foreign_result = await world.mutate(world.admin, turn_path, foreign)
    assert foreign_result.status_code == 201
    assert foreign_result.json()["result"] == denied.json()["result"]


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff"])
async def test_workspace_guidance_replay_revocation_and_input_bounds(
    sessions: SessionWorld, role: str
) -> None:
    world = sessions
    member, identity = await world.member(role=role)
    if role == "tenant_admin":
        await world.member(role="tenant_admin")
    created = await world.mutate(member, workspace_path(), {"request_key": str(uuid4())})
    assert created.status_code == 201, created.text
    target = workspace_path(suffix=f"/{created.json()['id']}/turns")
    payload = {
        "request_key": str(uuid4()),
        "body": "Guide synthetic@example.test",
        "page_hint": {"page": "business_orders"},
    }
    results = await asyncio.gather(*(world.mutate(member, target, payload) for _ in range(3)))
    assert sorted(response.status_code for response in results) == [200, 200, 201]
    assert len({response.json()["id"] for response in results}) == 1
    history = await member.get(target)
    assert history.status_code == 200, history.text
    [turn] = history.json()["items"]
    assert "synthetic@example.test" not in turn["body"]
    assert turn["result"]["instruction_key"] == "business_orders_help"
    assert turn["result"]["source"] == "application_guide"
    audit = await world.q(
        "SELECT details FROM console_audit WHERE actor_id=%s AND action='workspace.turn.completed'",
        identity["user"]["id"],
    )
    assert len(audit) == 1
    assert "synthetic@example.test" not in str(audit)
    for extra in (
        {"role": "platform_admin"},
        {"tenant_id": world.tenants[1]},
        {"actor": identity["user"]["id"]},
        {"attachments": []},
    ):
        response = await world.mutate(
            member, target, {**payload, "request_key": str(uuid4()), **extra}
        )
        assert response.status_code == 422
        assert "synthetic@example.test" not in response.text
    oversized = await member.post(target, content=b"x" * 32769, headers=world.headers(member))
    assert oversized.status_code == 413
    revoked = await world.mutate(
        world.admin,
        f"/console/v1/team/{identity['user']['id']}?client_id={world.tenants[0]}",
        {"is_enabled": False},
        method="PATCH",
    )
    assert revoked.status_code == 204, revoked.text
    assert (await member.get(target)).status_code == 401
    assert (await world.mutate(member, target, payload)).status_code == 401


async def test_workspace_audit_failure_rolls_back_turn_and_allows_retry(
    sessions: SessionWorld,
) -> None:
    world = sessions
    created = await world.mutate(
        world.admin, workspace_path(world.tenants[0]), {"request_key": str(uuid4())}
    )
    assert created.status_code == 201
    target = workspace_path(world.tenants[0], f"/{created.json()['id']}/turns")
    payload = {"request_key": str(uuid4()), "body": "Synthetic rollback guidance"}
    execute = db.q.execute

    async def fail_audit(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "console/audit.sql":
            raise RuntimeError("synthetic workspace audit failure")
        return await execute(name, *args, **kwargs)

    with patch.object(db.q, "execute", side_effect=fail_audit):
        with pytest.raises(RuntimeError, match="synthetic workspace audit failure"):
            await world.mutate(world.admin, target, payload)
    assert (await world.admin.get(target)).json()["items"] == []
    retried = await world.mutate(world.admin, target, payload)
    assert retried.status_code == 201, retried.text
    assert retried.json()["duplicate"] is False


async def test_workspace_history_pagination_and_database_tenant_boundaries(
    sessions: SessionWorld,
) -> None:
    world = sessions
    tenant = world.tenants[0]
    created = await world.mutate(world.admin, workspace_path(tenant), {"request_key": str(uuid4())})
    thread_id = created.json()["id"]
    target = workspace_path(tenant, f"/{thread_id}/turns")
    first = await world.mutate(
        world.admin, target, {"request_key": str(uuid4()), "body": "Synthetic guidance"}
    )
    assert first.status_code == 201, first.text
    await world.q(
        "INSERT INTO console_workspace_turns "
        "(client_id,thread_id,owner_user_id,session_id,request_key,fingerprint,body,"
        "action,page_key,result) "
        "SELECT client_id,thread_id,owner_user_id,session_id,uuid_generate_v4(),"
        "fingerprint,body,action,page_key,result FROM console_workspace_turns "
        "CROSS JOIN generate_series(1,50) WHERE id=%s",
        first.json()["id"],
    )
    history = (await world.admin.get(target)).json()
    assert len(history["items"]) == 50
    assert history["next_cursor"] == history["items"][-1]["id"]
    older = (await world.admin.get(f"{target}&before={history['next_cursor']}")).json()
    assert len(older["items"]) == 1
    assert older["next_cursor"] is None
    ids = [turn["id"] for turn in history["items"] + older["items"]]
    assert len(set(ids)) == 51 and ids == sorted(ids, reverse=True)
    await world.q(
        "INSERT INTO console_workspace_threads (client_id,owner_user_id,create_request_key) "
        "SELECT client_id,owner_user_id,uuid_generate_v4() FROM console_workspace_threads "
        "CROSS JOIN generate_series(1,21) WHERE id=%s",
        thread_id,
    )
    newest = (await world.admin.get(workspace_path(tenant))).json()
    assert len(newest["items"]) == 20
    older_threads = (
        await world.admin.get(f"{workspace_path(tenant)}&before={newest['next_cursor']}")
    ).json()
    assert len(older_threads["items"]) == 2
    assert older_threads["next_cursor"] is None
    assert len({thread["id"] for thread in newest["items"] + older_threads["items"]}) == 22

    async with world.pool.connection() as conn:
        cursor = await conn.execute("SELECT id FROM console_workspace_turns")
        assert await cursor.fetchall() == []
        cursor = await conn.execute("SELECT id FROM console_workspace_threads")
        assert await cursor.fetchall() == []
    async with tx(world.pool, world.tenants[1]) as conn:
        cursor = await conn.execute("SELECT id FROM console_workspace_turns")
        assert await cursor.fetchall() == []
    async with tx(world.pool, tenant) as conn:
        cursor = await conn.execute("SELECT id FROM console_workspace_turns")
        assert len(await cursor.fetchall()) == 51
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("DELETE FROM console_workspace_turns")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("UPDATE console_workspace_threads SET created_at=now()")
    grants = await world.q(
        "SELECT has_table_privilege('csa_ro','console_workspace_threads','SELECT') AS threads,"
        "has_table_privilege('csa_ro','console_workspace_turns','SELECT') AS turns"
    )
    assert grants == [{"threads": False, "turns": False}]


# --------------------------------------------------- assistant email: team send and attention


async def test_email_my_team_and_send_it_drafts_then_sends_once(planned, proposals_app) -> None:  # noqa: F811
    world, model = planned
    _, gmail = proposals_app
    world.app.state.email_service.model = CleanDraftModel()
    browser = await _mail_ready(world, "hr")
    ids = await _team_lead(world, browser)
    await _approves_team(world, browser)
    thread = await _thread(world, browser)

    def team(facts: dict[str, Any]) -> dict[str, Any]:
        if not facts["my_team"] or not facts["my_team"]["reports"]:
            return {"action": "answer", "markdown": "No team."}
        return {
            "action": "email_team",
            "subject": "Friday's meeting",
            "instructions": "The meeting on Friday moves to 3 pm",
            "send": True,
        }

    model.respond(team)
    turn = await _turn(
        world, browser, thread, "Email my team about Friday's meeting and send it", "email"
    )
    assert turn.status_code == 201, turn.text
    proposal = turn.json()["result"]["proposal"]
    assert proposal["kind"] == "email_send" and proposal["status"] == "requested"
    assert proposal["card"]["title"] == "Send email to 2 people"
    fields = {f["label"]: f["value"] for f in proposal["card"]["fields"]}
    assert fields["Not included"] == "Bee Report (no work email)"
    drafts = await world.q("SELECT thread_id, to_addresses, cc, bcc FROM email_drafts")
    assert drafts == [
        {
            "thread_id": None,
            "to_addresses": ["ali.report@example.test", "cai.report@example.test"],
            "cc": [],
            "bcc": [],
        }
    ]
    assert [r["action"] for r in await _turn_rows(world)] == ["email_team"]
    assert str(ids["lead"]) not in json.dumps(model.facts()["my_team"])

    done = await world.mutate(browser, f"{PROPOSALS}/{proposal['id']}/confirm")
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "executed" and done.json()["result"]["outcome"] == "sent"
    assert len(gmail.sends) == 1
    raw = base64.urlsafe_b64decode(gmail.sends[0]["raw"] + "==").decode()
    assert "ali.report@example.test" in raw and "cai.report@example.test" in raw
    replay = await world.mutate(browser, f"{PROPOSALS}/{proposal['id']}/confirm")
    assert replay.status_code == 200 and replay.json() == done.json()
    assert len(gmail.sends) == 1


async def test_what_needs_my_attention_answers_and_stores_no_content(planned) -> None:  # noqa: F811
    world, model = planned
    browser = await _mail_ready(world)
    await _sync(world, browser)
    thread = await _thread(world, browser)
    model.reply({"action": "inbox_summary"})
    turn = await _turn(world, browser, thread, "What needs my attention today?", "email")
    assert turn.status_code == 201, turn.text
    answer = turn.json()["result"]
    assert answer["action"] == "answer"
    assert "Order MGL-TEST-005" in answer["markdown"]
    assert "Customer asks where order MGL-TEST-005 is." in answer["markdown"]
    assert "Check order MGL-TEST-005 status" in answer["markdown"]

    rows = await world.q("SELECT action, body, result::text t FROM console_workspace_turns")
    assert [r["action"] for r in rows] == ["inbox_summary"]
    for needle in ("MGL-TEST-005", "Customer asks", "aina@example.test", "Demo Aina", "Weekly"):
        assert needle not in rows[0]["t"], needle
    assert "Inbox summary: 2 items" in rows[0]["t"]
    history = await browser.get(f"/console/v1/workspace/conversations/{thread}/turns")
    assert history.status_code == 200
    assert "MGL-TEST-005" not in history.text


async def test_attention_refusals_are_plain_answers(planned) -> None:  # noqa: F811
    world, model = planned
    browser = await _mail_ready(world)
    await _sync(world, browser)
    unconnected, _ = await world.member(role="tenant_staff")

    async def ask(who: httpx.AsyncClient) -> str:
        model.reply({"action": "inbox_summary"})
        thread = await _thread(world, who)
        turn = await _turn(world, who, thread, "What needs my attention today?", "email")
        assert turn.status_code == 201, turn.text
        assert turn.json()["result"]["action"] == "answer"
        return turn.json()["result"]["markdown"]

    assert await ask(unconnected) == "Connect your Gmail on the Email assistant tab first."
    world.app.state.email_service.model = None  # nothing is analysed yet, so nothing to show
    assert await ask(browser) == (
        "I couldn't summarise your inbox just now. Try again in a few minutes."
    )
    await world.q("UPDATE email_settings SET enabled=false WHERE client_id=%s", world.tenants[0])
    assert await ask(browser) == "The Email assistant is turned off for your workspace."
    rows = await world.q("SELECT action, result::text t FROM console_workspace_turns")
    assert [r["action"] for r in rows] == ["inbox_summary"] * 3
    for row in rows:
        assert "MGL-TEST-005" not in row["t"] and "aina@example.test" not in row["t"]
