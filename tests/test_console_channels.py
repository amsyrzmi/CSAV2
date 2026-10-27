"""Connect a WhatsApp number from the console; the token is sealed and never returned."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from csa.console.channels import build_channels_router
from csa.core import crypto

from tests.conftest import requires_docker
from tests.test_console_inbox import seed
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TOKEN = "EAAG-synthetic-access-token-0123456789"
PATH = "/console/v1/channels/whatsapp"


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch) -> None:
    await sessions.q(
        "DELETE FROM webhook_endpoints WHERE credential_id IN "
        "(SELECT id FROM channel_credentials WHERE channel='whatsapp')"
    )
    await sessions.q("DELETE FROM channel_credentials WHERE channel='whatsapp'")
    sessions.app.include_router(build_channels_router())
    monkeypatch.setattr(
        "csa.console.channels.get_settings",
        lambda: SimpleNamespace(whatsapp_enabled=True, PUBLIC_BASE_URL="https://csa.example.test"),
    )
    monkeypatch.setattr(crypto, "_key", lambda: b"\x07" * 32)
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "customer_support")


def url(w: SessionWorld, tenant: int = 0) -> str:
    return f"{PATH}?client_id={w.tenants[tenant]}"


def body(number: str = "109876543210987") -> dict:
    return {
        "phone_number_id": number,
        "waba_id": "20001234567",
        "display_phone": "+60 12-345 6789",
        "access_token": TOKEN,
    }


async def test_tenant_admin_connects_number_and_token_is_encrypted_at_rest(
    sessions: SessionWorld,
) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    response = await w.mutate(admin, PATH, body(), method="PUT")
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text
    out = response.json()
    assert out["connected"] and out["enabled"]
    assert out["phone_number_id_last4"] == "0987" and out["waba_id"] == "20001234567"
    assert out["callback_url"] == "https://csa.example.test/channels/whatsapp"
    rows = await w.q(
        "SELECT id,access_token,shop_id,meta FROM channel_credentials WHERE client_id=%s",
        w.tenants[0],
    )
    assert len(rows) == 1 and rows[0]["access_token"] != TOKEN
    aad = crypto.credential_aad("channel_credentials", "access_token", str(rows[0]["id"]))
    assert crypto.decrypt(rows[0]["access_token"], aad) == TOKEN
    assert rows[0]["shop_id"] == "109876543210987"
    assert rows[0]["meta"] == {"waba_id": "20001234567", "display_phone": "+60 12-345 6789"}
    endpoint = await w.q(
        "SELECT public_id,name,is_enabled FROM webhook_endpoints WHERE credential_id=%s",
        rows[0]["id"],
    )
    assert len(endpoint) == 1 and endpoint[0]["is_enabled"]
    assert endpoint[0]["name"] == "WhatsApp +60 12-345 6789"
    audits = await w.q(
        "SELECT details::text d FROM console_audit WHERE client_id=%s AND action ~ '[.]connected'",
        w.tenants[0],
    )
    assert len(audits) == 1 and TOKEN not in audits[0]["d"]
    again = await w.mutate(admin, PATH, body(), method="PUT")
    assert again.status_code == 200
    assert len(await w.q("SELECT id FROM webhook_endpoints WHERE client_id=%s", w.tenants[0])) == 1
    got = await admin.get(PATH)
    assert got.status_code == 200 and TOKEN not in got.text
    assert got.json()["display_phone"] == "+60 12-345 6789"


async def test_staff_and_reader_cannot_connect_but_can_read(sessions: SessionWorld) -> None:
    w = sessions
    for role in ("tenant_staff", "read_only"):
        member, _ = await w.member(role=role)
        assert (await w.mutate(member, PATH, body(), method="PUT")).status_code == 403
        assert (await w.mutate(member, PATH, method="DELETE")).status_code == 403
        got = await member.get(PATH)
        assert got.status_code == 200 and got.json()["connected"] is False
    assert not await w.q(
        "SELECT id FROM channel_credentials WHERE channel='whatsapp' AND client_id=ANY(%s::uuid[])",
        w.tenants,
    )


async def test_cross_tenant_connect_is_refused(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    assert (await w.mutate(admin, url(w, 1), body(), method="PUT")).status_code == 403
    assert (await admin.get(url(w, 1))).status_code == 403
    assert not await w.q(
        "SELECT id FROM channel_credentials WHERE channel='whatsapp' AND client_id=ANY(%s::uuid[])",
        w.tenants,
    )
    assert (await w.mutate(w.admin, url(w, 1), body(), method="PUT")).status_code == 200


async def test_number_in_use_by_other_tenant_is_409(sessions: SessionWorld) -> None:
    w = sessions
    assert (await w.mutate(w.admin, url(w, 0), body(), method="PUT")).status_code == 200
    clash = await w.mutate(w.admin, url(w, 1), body(), method="PUT")
    assert clash.status_code == 409 and clash.json()["detail"] == "whatsapp_number_in_use"
    assert not await w.q("SELECT id FROM channel_credentials WHERE client_id=%s", w.tenants[1])


async def test_disconnect_disables_endpoint_and_erases_token(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    await w.mutate(admin, PATH, body(), method="PUT")
    assert (await w.mutate(admin, PATH, method="DELETE")).status_code == 204
    rows = await w.q(
        "SELECT c.access_token,e.is_enabled,e.disabled_reason FROM channel_credentials c "
        "JOIN webhook_endpoints e ON e.credential_id=c.id WHERE c.client_id=%s",
        w.tenants[0],
    )
    assert rows == [{"access_token": None, "is_enabled": False, "disabled_reason": "disconnected"}]
    got = (await admin.get(PATH)).json()
    assert got["connected"] is False and got["enabled"] is False
    assert await w.q(
        "SELECT 1 FROM console_audit WHERE action LIKE '%%.disconnected' AND client_id=%s",
        w.tenants[0],
    )
    back = await w.mutate(admin, PATH, body(), method="PUT")
    assert back.status_code == 200 and back.json()["connected"]


async def test_package_gate_applies(sessions: SessionWorld) -> None:
    w = sessions
    created = await w.mutate(
        w.admin,
        "/console/v1/admin/clients",
        {"slug": f"synthetic-{uuid4()}", "name": "No package"},
    )
    response = await w.admin.get(f"{PATH}?client_id={created.json()['id']}")
    assert response.status_code == 403 and "package_inactive" in response.text


async def test_inbox_lists_whatsapp_conversations_with_channel_and_filter(
    sessions: SessionWorld,
) -> None:
    w = sessions
    await seed(w)
    contact, conversation = uuid4(), uuid4()
    await w.q("INSERT INTO contacts(id,client_id) VALUES (%s,%s)", contact, w.tenants[0])
    await w.q(
        "INSERT INTO conversations(id,client_id,contact_id,channel,channel_thread) "
        "VALUES (%s,%s,%s,'whatsapp','60123456789')",
        conversation,
        w.tenants[0],
        contact,
    )
    base = f"/console/v1/inbox/conversations?client_id={w.tenants[0]}"
    both = (await w.admin.get(base)).json()["items"]
    assert {item["channel"] for item in both} == {"telegram", "whatsapp"}
    only = (await w.admin.get(base + "&channel=whatsapp")).json()["items"]
    assert [UUID(item["id"]) for item in only] == [conversation]
    telegram = (await w.admin.get(base + "&channel=telegram")).json()["items"]
    assert [item["channel"] for item in telegram] == ["telegram"]
    assert (await w.admin.get(base + "&channel=email")).status_code == 422


async def test_dead_delivery_exposes_its_failure_code(sessions: SessionWorld) -> None:
    w = sessions
    _, conversation = await seed(w)
    message = (
        await w.q("SELECT id,trace_id FROM messages WHERE conversation_id=%s LIMIT 1", conversation)
    )[0]
    endpoint = (await w.q("SELECT id FROM webhook_endpoints WHERE client_id=%s", w.tenants[0]))[0]
    await w.q(
        "INSERT INTO outbound_deliveries(client_id,endpoint_id,trace_id,conversation_id,"
        "message_id,status,last_error) VALUES (%s,%s,%s,%s,%s,'dead','outside_service_window')",
        w.tenants[0],
        endpoint["id"],
        message["trace_id"],
        conversation,
        message["id"],
    )
    path = f"/console/v1/inbox/conversations/{conversation}/messages?client_id={w.tenants[0]}"
    items = (await w.admin.get(path)).json()["items"]
    assert items[0]["delivery_status"] == "dead"
    assert items[0]["delivery_error"] == "outside_service_window"


async def test_disconnect_releases_the_number_for_another_tenant(sessions: SessionWorld) -> None:
    w = sessions
    number = body()["phone_number_id"]
    assert (await w.mutate(w.admin, url(w, 0), body(), method="PUT")).status_code == 200
    assert (await w.mutate(w.admin, url(w, 0), method="DELETE")).status_code == 204
    taken = await w.mutate(w.admin, url(w, 1), body(), method="PUT")
    assert taken.status_code == 200, taken.text
    first = (await w.admin.get(url(w, 0))).json()
    assert first["connected"] is False and first["connected_at"] is None
    route = await w.q("SELECT client_id FROM resolve_whatsapp_route(%s)", number)
    assert [str(row["client_id"]) for row in route] == [w.tenants[1]]
