"""Authenticated workspace assistant behavior on disposable synthetic tenants."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.agents.ecommerce.models import LiveStock, SkuStock
from csa.core import db
from csa.core.tenancy import tx
from csa.marketplace import reads

from tests.conftest import requires_docker
from tests.test_console_business import SHIP_TO, put_tiktok_order, seed_connection_status
from tests.test_console_services import activate
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
    # No TikTok Shop is connected for this tenant, and the read says so.
    assert result["marketplace_sync"] == "not_connected"
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


async def test_email_my_team_in_malay_names_who_is_not_included_in_malay(
    planned,  # noqa: F811
    proposals_app,  # noqa: F811
) -> None:
    from csa.workspace.service import _TEXT

    world, model = planned
    world.app.state.email_service.model = CleanDraftModel()
    browser = await _mail_ready(world, "hr")
    await _team_lead(world, browser)
    await _approves_team(world, browser)
    thread = await _thread(world, browser)
    model.reply(
        {
            "action": "email_team",
            "subject": "Mesyuarat Jumaat",
            "instructions": "Mesyuarat pada hari Jumaat dialihkan ke pukul 3 petang",
        }
    )
    turn = await _turn(
        world, browser, thread, "Tolong e-mel pasukan saya tentang mesyuarat Jumaat", "email"
    )
    assert turn.status_code == 201, turn.text
    assert turn.json()["language"] == "ms"
    fields = turn.json()["result"]["proposal"]["card"]["fields"]
    names = "Bee Report"
    skipped = {
        "label": _TEXT["ms"]["not_included"],
        "value": f"{names} ({_TEXT['ms']['no_work_email']})",
    }
    assert skipped in fields
    assert skipped == {"label": "Tidak disertakan", "value": "Bee Report (tiada e-mel kerja)"}


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


async def test_legacy_turn_carries_thread_language(planned) -> None:  # noqa: F811
    w, model = planned
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    model.reply({"action": "answer", "markdown": "Baik."})
    first = await _turn(w, browser, thread, "tolong buka halaman pesanan", "overview")
    assert first.status_code == 201, first.text
    assert first.json()["language"] == "ms"
    legacy = await w.mutate(
        browser,
        workspace_path(suffix=f"/{thread}/turns"),
        {"request_key": str(uuid4()), "body": "ok", "page_hint": {"page": "business_orders"}},
    )
    assert legacy.status_code == 201, legacy.text
    assert legacy.json()["language"] == "ms"
    english = await w.mutate(
        browser,
        workspace_path(suffix=f"/{thread}/turns"),
        {
            "request_key": str(uuid4()),
            "body": "show me the page",
            "page_hint": {"page": "business_orders"},
        },
    )
    assert english.json()["language"] == "en"
    items = (await browser.get(workspace_path(suffix=f"/{thread}/turns"))).json()["items"]
    assert [item["language"] for item in items] == ["en", "ms", "ms"]


# ---- Task 20: TikTok orders, products and stock for staff (read-only, spec 6.8) ----

READ_RECORD = {"action": "answer", "markdown": "Here it is.", "reads": ["read_current_record"]}


async def put_product(
    w: SessionWorld,
    tenant: str,
    store: str,
    name: str,
    variants: list[tuple[str, str | None, int | None]],
    *,
    synced_at: datetime | None = None,
    status: str = "ACTIVATE",
) -> list[str]:
    """One TikTok product of ``store`` with one listing row per variant; synthetic ids only."""
    product = f"SYNTH-P-{uuid4()}"
    ids = []
    for variant, sku, qty in variants:
        row = await w.q(
            "INSERT INTO marketplace_listings (client_id,store_id,channel,external_product_id,"
            "external_sku_id,product_name,variant_name,seller_sku,price,currency,stock_qty,"
            "product_status,synced_at) VALUES (%s,%s,'tiktok_shop',%s,%s,%s,%s,%s,9.90,'MYR',"
            "%s,%s,%s) RETURNING id",
            tenant,
            store,
            product,
            f"SYNTH-SKU-{uuid4()}",
            name,
            variant,
            sku,
            qty,
            status,
            synced_at or datetime.now(UTC) - timedelta(hours=2),
        )
        ids.append(str(row[0]["id"]))
    return ids


async def _shop_tenant(w: SessionWorld, *packages: str) -> tuple[str, str]:
    """tenants[0] with these packages and one connected TikTok store (its own synthetic shop)."""
    tenant = w.tenants[0]
    if packages:
        await activate(w, tenant, *packages)
    return tenant, await seed_connection_status(w, tenant, "authorization_recorded")


async def test_product_record_gated_by_business_package(planned) -> None:  # noqa: F811
    w, model = planned
    tenant, store = await _shop_tenant(w)
    mug = await put_product(
        w, tenant, store, "Synthetic Mug", [("Black / M", "MUG-BM", 5), ("White / L", None, 7)]
    )
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    record = {"kind": "product", "id": mug[0]}

    model.reply(READ_RECORD)
    locked = await _turn(w, browser, thread, "What is this product?", "business", record=record)
    assert locked.status_code == 201, locked.text
    assert model.facts()["record_summary"] == "The open product record's service is not active."
    assert "Synthetic Mug" not in locked.text

    await activate(w, tenant, "orders_products")
    model.reply(READ_RECORD)
    opened = await _turn(w, browser, thread, "What is this product?", "business", record=record)
    assert opened.status_code == 201, opened.text
    summary = model.facts()["record_summary"]
    assert summary == (
        f"TikTok Shop product \u201cSynthetic Mug\u201d (listing {mug[0]}): 2 variants, "
        "12 in stock in our copy, synced 2 hours ago."
    )
    assert opened.json()["result"]["markdown"].endswith(summary)
    for private in ("SYNTH-P-", "SYNTH-SKU-", store, "MUG-BM", mug[1]):
        assert private not in summary, private

    # Another tenant's listing and a malformed id read as unavailable, never as its data.
    other = w.tenants[1]
    foreign_store = await seed_connection_status(w, other, "authorization_recorded")
    (foreign,) = await put_product(w, other, foreign_store, "Foreign Mug", [("One", None, 3)])
    for record_id in (foreign, "not-a-uuid", str(uuid4())):
        model.reply(READ_RECORD)
        turn = await _turn(
            w,
            browser,
            thread,
            "What is this?",
            "business",
            record={"kind": "product", "id": record_id},
        )
        assert turn.status_code == 201, turn.text
        assert model.facts()["record_summary"] == "The open product record is not available to you."
        assert "Foreign Mug" not in turn.text


async def test_read_only_member_cannot_open_product_context(planned) -> None:  # noqa: F811
    w, model = planned
    tenant, store = await _shop_tenant(w, "orders_products")
    (listing,) = await put_product(w, tenant, store, "Synthetic Mug", [("One", None, 4)])
    staff, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, staff)
    reader, _ = await w.member(role="read_only")
    for record in ({"kind": "product", "id": listing}, None):
        refused = await _turn(w, reader, thread, "How many do we have?", "business", record=record)
        assert refused.status_code == 403, refused.text
        assert "Synthetic Mug" not in refused.text
    own = await w.mutate(reader, workspace_path(), {"request_key": str(uuid4())})
    assert own.status_code == 403
    assert model.calls == []
    assert await _turn_rows(w) == []


async def test_summarize_tiktok_order_is_redacted_and_says_synced(planned) -> None:  # noqa: F811
    w, model = planned
    tenant, _ = await _shop_tenant(w, "orders_products")
    order_id = await put_tiktok_order(w, tenant)
    await w.q("UPDATE orders SET synced_at=now()-interval '3 minutes' WHERE id=%s", order_id)
    local_id = await put_tiktok_order(w, tenant, external=None, lines=False)
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)

    model.reply(READ_RECORD)
    turn = await _turn(
        w,
        browser,
        thread,
        "Summarise this order",
        "business",
        record={"kind": "order", "id": order_id},
    )
    assert turn.status_code == 201, turn.text
    summary = model.facts()["record_summary"]
    assert summary == (
        f"Order {order_id}: status processing, payment paid, total 20.00 MYR. "
        "TikTok \u00b7 synced 3 min ago, TikTok status AWAITING_SHIPMENT."
    )
    private = (
        SHIP_TO["recipient_name"],
        SHIP_TO["phone"],
        SHIP_TO["address_text"],
        "Blue Mug",
        "Amber Plate",
        "SKU-A",
        "SKU-B",
        "SYNTH-TRACK",
        "Synthetic Carrier",
    )
    stored = json.dumps([row["result"] for row in await _turn_rows(w)])
    for text in (summary, turn.text, model.calls[-1][2], stored):
        for needle in private:
            assert needle not in text, needle

    # A TikTok-channel order with no TikTok order number is a local order: no TikTok line.
    model.reply(READ_RECORD)
    await _turn(
        w,
        browser,
        thread,
        "Summarise this order",
        "business",
        record={"kind": "order", "id": local_id},
    )
    assert model.facts()["record_summary"] == (
        f"Order {local_id}: status processing, payment paid, total 20.00 MYR."
    )


async def test_order_read_marketplace_sync_is_truthful(sessions: SessionWorld) -> None:
    """The legacy order read says whether a TikTok Shop is connected (spec 6.8)."""
    world = sessions
    tenant = world.tenants[0]
    order_id = await put_tiktok_order(world, tenant)
    thread = await world.mutate(world.admin, workspace_path(tenant), {"request_key": str(uuid4())})
    turn_path = workspace_path(tenant, f"/{thread.json()['id']}/turns")

    async def read() -> dict[str, Any]:
        payload = {
            "request_key": str(uuid4()),
            "body": "Read this order",
            "page_hint": {
                "page": "business_order",
                "entity": {"kind": "local_order", "order_id": order_id},
            },
        }
        response = await world.mutate(world.admin, turn_path, payload)
        assert response.status_code == 201, response.text
        assert "Synthetic Buyer" not in response.text and "1 Synthetic Lane" not in response.text
        return response.json()["result"]

    assert (await read())["marketplace_sync"] == "not_connected"
    await seed_connection_status(world, tenant, "authorization_recorded")
    assert (await read())["marketplace_sync"] == "connected"
    await world.q("UPDATE channel_credentials SET disconnected_at=now() WHERE client_id=%s", tenant)
    assert (await read())["marketplace_sync"] == "not_connected"


def _live(listing_text: str, *, checked_live: bool, age: timedelta) -> LiveStock:
    listing = UUID(listing_text)
    return LiveStock(
        listing_id=listing,
        product_name="Synthetic Mug",
        skus=(
            SkuStock(
                listing_id=listing,
                external_sku_id="SYNTH-SKU-LIVE-1",
                variant_name="Black / M",
                seller_sku="MUG-BM",
                stock_qty=8,
                price=None,
                currency="MYR",
            ),
            SkuStock(
                listing_id=None,
                external_sku_id="SYNTH-SKU-LIVE-2",
                variant_name="White / L",
                seller_sku=None,
                stock_qty=0,
                price=None,
                currency="MYR",
            ),
        ),
        checked_live=checked_live,
        as_of=datetime.now(UTC) - age,
        sellable=True,
    )


async def test_stock_read_live_and_fallback_wording(
    planned,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w, model = planned
    tenant, store = await _shop_tenant(w, "orders_products")
    mug = await put_product(
        w, tenant, store, "Synthetic Mug", [("Black / M", "MUG-BM", 5), ("White / L", None, 7)]
    )
    products_before = await w.q("SELECT count(*) n FROM products")
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    sentinel = object()
    w.app.state.marketplace_live = sentinel
    calls: list[tuple[str, object]] = []
    real_live_stock = reads.live_stock

    def fake(checked_live: bool, age: timedelta):  # type: ignore[no-untyped-def]
        async def live_stock(  # type: ignore[no-untyped-def]
            pool, client_id, listing_id, *, live, clock=None, include_unsellable=False
        ):
            assert str(client_id) == tenant
            assert include_unsellable is True  # staff see every SKU, on sale or not
            calls.append((str(listing_id), live))
            return _live(str(listing_id), checked_live=checked_live, age=age)

        return live_stock

    async def ask(body: str, proposal: dict[str, Any], record: dict | None = None) -> dict:
        model.reply(proposal)
        turn = await _turn(w, browser, thread, body, "business", record=record)
        assert turn.status_code == 201, turn.text
        return turn.json()

    # Live: exact per-variant counts and "checked live just now".
    monkeypatch.setattr(reads, "live_stock", fake(True, timedelta()))
    found = await ask(
        "How many synthetic mugs do we have on TikTok?",
        {"action": "product_stock_read", "query": "synthetic mug"},
    )
    result = found["result"]
    assert result["action"] == "product_stock_read" and result["source"] == "tiktok_shop"
    assert result["product"] == "Synthetic Mug" and result["checked_live"] is True
    assert result["variants"] == [
        {"variant": "Black / M", "seller_sku": "MUG-BM", "stock": 8},
        {"variant": "White / L", "seller_sku": None, "stock": 0},
    ]
    assert result["total"] == 8 and result["on_sale"] is True
    assert result["note"] == "Checked live just now."
    assert calls == [(mug[0], sentinel)]
    assert "SYNTH-SKU-LIVE" not in json.dumps(found) and "SYNTH-P-" not in json.dumps(found)

    # The live read failed: our copy, "as of 2 hours ago (live check failed)".
    monkeypatch.setattr(reads, "live_stock", fake(False, timedelta(hours=2, minutes=5)))
    copy = await ask(
        "How many synthetic mugs do we have on TikTok?",
        {"action": "product_stock_read", "query": "synthetic mug"},
    )
    assert copy["result"]["checked_live"] is False
    assert copy["result"]["note"] == "As of 2 hours ago (live check failed)."
    # The same in Malay.
    malay = await ask(
        "Berapa banyak stok mug ini ada di TikTok?",
        {"action": "product_stock_read"},
        record={"kind": "product", "id": mug[1]},
    )
    assert malay["language"] == "ms"
    assert malay["result"]["note"] == "Setakat 2 jam lalu (semakan langsung gagal)."
    assert calls[-1] == (mug[1], sentinel)  # the open product record

    # The real reader with TikTok off answers from our copy, never by writing anything.
    monkeypatch.setattr(reads, "live_stock", real_live_stock)
    w.app.state.marketplace_live = None
    real = await ask(
        "How many synthetic mugs do we have on TikTok?",
        {"action": "product_stock_read", "listing_id": mug[0]},
    )
    assert real["result"]["variants"] == [
        {"variant": "Black / M", "seller_sku": "MUG-BM", "stock": 5},
        {"variant": "White / L", "seller_sku": None, "stock": 7},
    ]
    assert real["result"]["total"] == 12 and real["result"]["checked_live"] is False
    assert real["result"]["note"] == "As of 2 hours ago (live check failed)."
    assert await w.q("SELECT count(*) n FROM products") == products_before
    assert await w.q("SELECT count(*) n FROM console_proposals") == [{"n": 0}]
    stock_rows = await w.q(
        "SELECT stock_qty FROM marketplace_listings WHERE client_id=%s ORDER BY stock_qty", tenant
    )
    assert [row["stock_qty"] for row in stock_rows] == [5, 7]
    stored = await _turn_rows(w)
    assert [row["action"] for row in stored] == ["answer"] * 4
    assert [row["result"]["action"] for row in stored] == ["product_stock_read"] * 4


async def test_stock_read_asks_which_product_and_refuses_without_a_shop(
    planned,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w, model = planned
    tenant, store = await _shop_tenant(w, "orders_products")
    products = [
        (await put_product(w, tenant, store, f"Synthetic Mug {i}", [("One", None, i)]))[0]
        for i in range(1, 8)
    ]
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    calls: list[str] = []

    async def no_read(*args: Any, **kwargs: Any) -> None:
        calls.append("read")
        raise AssertionError("no stock read for an ambiguous or refused question")

    monkeypatch.setattr(reads, "live_stock", no_read)

    async def ask(body: str, proposal: dict[str, Any], who: httpx.AsyncClient = browser) -> dict:
        model.reply(proposal)
        turn = await _turn(w, who, await _thread(w, who), body, "business")
        assert turn.status_code == 201, turn.text
        return turn.json()["result"]

    choose = await ask(
        "How many mugs do we have?", {"action": "product_stock_read", "query": "synthetic mug"}
    )
    assert choose["action"] == "clarification"
    assert choose["question"] == "Several TikTok Shop products match. Which one do you mean?"
    assert len(choose["options"]) == 5
    assert {option["value"] for option in choose["options"]} <= set(products)
    assert [option["label"] for option in choose["options"]] == [
        f"Synthetic Mug {i}" for i in range(1, 6)
    ]
    nothing = await ask("How many teapots?", {"action": "product_stock_read", "query": "teapot"})
    assert nothing == {
        "action": "answer",
        "markdown": "I couldn't find that product on your TikTok Shop.",
    }
    unnamed = await ask("How many do we have?", {"action": "product_stock_read"})
    assert unnamed == {
        "action": "clarification",
        "question": "Which TikTok Shop product do you mean? Type its name or SKU.",
        "options": [],
    }
    assert calls == []
    assert thread

    # A tenant with the package but no connected shop, and one without the package.
    other = w.tenants[1]
    await activate(w, other, "orders_products")
    elsewhere, _ = await w.member(role="tenant_staff", tenant=other)
    no_shop = await ask(
        "How many mugs?", {"action": "product_stock_read", "query": "mug"}, elsewhere
    )
    assert no_shop == {
        "action": "answer",
        "markdown": "No TikTok Shop is connected. An Admin can connect one on the Store "
        "connections page in Business management.",
    }
    await w.q(
        "UPDATE console_service_packages SET status='suspended' "
        "WHERE client_id=%s AND package='orders_products'",
        tenant,
    )
    locked = await ask("How many mugs?", {"action": "product_stock_read", "query": "mug"})
    assert locked["action"] == "answer"
    assert locked["markdown"].startswith("The Business management tab needs the ")
    assert calls == []


async def test_post_proposals_rejects_plan_with_422(proposals_app) -> None:  # noqa: F811
    """Only the assistant makes a plan: the direct create route never stores one."""
    w, _ = proposals_app
    browser, _ = await w.member(role="tenant_staff")
    step = {"kind": "email_export", "payload": {"draft_id": str(uuid4())}, "version": None}
    refused = await w.mutate(browser, PROPOSALS, {"kind": "plan", "payload": {"steps": [step]}})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == "plan_not_creatable"
    assert await w.q("SELECT count(*) n FROM console_proposals WHERE kind='plan'") == [{"n": 0}]
