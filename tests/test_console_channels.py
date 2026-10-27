"""Connect a WhatsApp number from the console; the token is sealed and never returned."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from apps.worker.main import IngressWorker
from csa.channels.whatsapp_ingress import WhatsAppIngress
from csa.console.channels import build_channels_router
from csa.core import crypto
from csa.core.db import build_pool

from tests.conftest import requires_docker
from tests.test_console_inbox import path, seed
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TOKEN = "EAAG-synthetic-access-token-0123456789"
PATH = "/console/v1/channels/whatsapp"
GRAPH = "https://graph.example.test/v99.0"
GRAPH_DISPLAY = "+60 12-345 6789"


@dataclass
class GraphStub:
    """Answers the connect-time `GET /{phone_number_id}` check; `status=0` drops the link."""

    status: int = 200
    requests: list[httpx.Request] = field(default_factory=list)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status == 0:
            raise httpx.ConnectError("synthetic outage")
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"code": 190, "message": "x"}})
        return httpx.Response(
            200,
            json={
                "display_phone_number": GRAPH_DISPLAY,
                "verified_name": "Kedai Synthetic",
                "id": request.url.path.rsplit("/", 1)[-1],
            },
        )


@pytest.fixture()
async def graph(sessions: SessionWorld) -> AsyncIterator[GraphStub]:
    stub = GraphStub()
    async with httpx.AsyncClient(transport=httpx.MockTransport(stub.handle)) as client:
        sessions.app.state.whatsapp_http = client
        yield stub


@pytest.fixture(autouse=True)
async def packages(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch, graph: GraphStub
) -> None:
    await sessions.q(
        "DELETE FROM webhook_endpoints WHERE credential_id IN "
        "(SELECT id FROM channel_credentials WHERE channel='whatsapp')"
    )
    await sessions.q("DELETE FROM channel_credentials WHERE channel='whatsapp'")
    sessions.app.include_router(build_channels_router())
    monkeypatch.setattr(
        "csa.console.channels.get_settings",
        lambda: SimpleNamespace(
            whatsapp_enabled=True,
            PUBLIC_BASE_URL="https://csa.example.test",
            WHATSAPP_CALLBACK_BASE_URL=None,
            WHATSAPP_GRAPH_BASE_URL=GRAPH,
        ),
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
        "access_token": TOKEN,
    }


async def test_tenant_admin_connects_number_and_token_is_encrypted_at_rest(
    sessions: SessionWorld, graph: GraphStub
) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    response = await w.mutate(admin, PATH, body(), method="PUT")
    assert response.status_code == 200, response.text
    assert TOKEN not in response.text
    # The number is proven with the supplied token before anything is stored.
    check = graph.requests[0]
    assert check.method == "GET"
    assert str(check.url).startswith(f"{GRAPH}/109876543210987?")
    assert check.url.params["fields"] == "display_phone_number,verified_name"
    assert check.headers["Authorization"] == f"Bearer {TOKEN}"
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
    assert rows[0]["meta"] == {"waba_id": "20001234567", "display_phone": GRAPH_DISPLAY}
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
    assert got.json()["display_phone"] == GRAPH_DISPLAY


@pytest.mark.parametrize(
    ("graph_status", "status", "detail"),
    [
        (401, 422, "whatsapp_token_rejected"),
        (400, 422, "whatsapp_token_rejected"),
        (404, 422, "whatsapp_token_rejected"),
        (503, 502, "whatsapp_unreachable"),
        (0, 502, "whatsapp_unreachable"),
    ],
)
async def test_connect_refuses_a_number_graph_does_not_confirm(
    sessions: SessionWorld,
    graph: GraphStub,
    capsys: pytest.CaptureFixture[str],
    graph_status: int,
    status: int,
    detail: str,
) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    graph.status = graph_status
    response = await w.mutate(admin, PATH, body(), method="PUT")
    assert response.status_code == status, response.text
    assert response.json()["detail"] == detail
    assert TOKEN not in response.text
    assert TOKEN not in "".join(capsys.readouterr())
    assert not await w.q("SELECT id FROM channel_credentials WHERE client_id=%s", w.tenants[0])
    assert not await w.q(
        "SELECT 1 FROM console_audit WHERE action LIKE 'channels.whatsapp.%%' AND client_id=%s",
        w.tenants[0],
    )


async def test_connect_takes_the_display_number_from_graph_only(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    claimed = {**body(), "display_phone": "+60 19-999 9999"}
    response = await w.mutate(admin, PATH, claimed, method="PUT")
    assert response.status_code == 422
    assert TOKEN not in response.text


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
    assert items[0]["provider_status"] is None and items[0]["provider_error"] is None


async def test_failed_status_callback_reaches_the_inbox(sessions: SessionWorld) -> None:
    """Meta reports most WhatsApp failures after the send returned 200."""
    w = sessions
    _, conversation = await seed(w)
    message = (
        await w.q("SELECT id,trace_id FROM messages WHERE conversation_id=%s LIMIT 1", conversation)
    )[0]
    endpoint = (await w.q("SELECT id FROM webhook_endpoints WHERE client_id=%s", w.tenants[0]))[0]
    await w.q(
        "INSERT INTO outbound_deliveries(client_id,endpoint_id,trace_id,conversation_id,"
        "message_id,status,provider_message_id,provider_status,provider_error) "
        "VALUES (%s,%s,%s,%s,%s,'delivered','wamid.LATE','failed','131047')",
        w.tenants[0],
        endpoint["id"],
        message["trace_id"],
        conversation,
        message["id"],
    )
    path = f"/console/v1/inbox/conversations/{conversation}/messages?client_id={w.tenants[0]}"
    response = await w.admin.get(path)
    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["delivery_status"] == "delivered"
    assert item["provider_status"] == "failed"
    assert item["provider_error"] == "131047"


async def test_staff_inbox_reply_on_whatsapp_is_queued_on_the_whatsapp_endpoint(
    sessions: SessionWorld,
) -> None:
    """Ingress, worker, take over and reply: the staff reply uses the WhatsApp route."""
    w = sessions
    number = "309876543210987"
    assert (await w.mutate(w.admin, url(w), body(number), method="PUT")).status_code == 200
    endpoint = (
        await w.q(
            "SELECT e.id FROM webhook_endpoints e JOIN channel_credentials c "
            "ON c.id=e.credential_id WHERE c.client_id=%s AND c.channel='whatsapp'",
            w.tenants[0],
        )
    )[0]["id"]
    secret = "synthetic-app-secret"
    delivery = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "1", "phone_number_id": number},
                            "contacts": [{"profile": {"name": "Aina"}, "wa_id": "60123456789"}],
                            "messages": [
                                {
                                    "from": "60123456789",
                                    "id": f"wamid.{uuid4().hex}",
                                    "timestamp": "1791676800",
                                    "type": "text",
                                    "text": {"body": "Is my parcel out?"},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    raw = json.dumps(delivery).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    ingress = WhatsAppIngress(w.pool, app_secret=secret, verify_token="synthetic-verify")
    (receipt,) = await ingress.receive(signature, raw)

    owner_pool = build_pool(w.owner_dsn, max_size=2)
    await owner_pool.open(wait=True)
    try:
        worker = IngressWorker(owner_pool, w.pool, lock_conninfo=w.owner_dsn)
        # The queue is shared with other tests: work until this turn is complete.
        for _ in range(50):
            done = await w.q(
                "SELECT 1 FROM ingress_events WHERE trace_id=%s AND status='completed'",
                receipt.trace_id,
            )
            if done or not await worker.process_once():
                break
    finally:
        await owner_pool.close()
    conversation = (
        await w.q(
            "SELECT id FROM conversations WHERE client_id=%s AND channel='whatsapp'",
            w.tenants[0],
        )
    )[0]["id"]

    taken = await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/takeover"),
        {"request_key": str(uuid4()), "expected_version": 0},
    )
    assert taken.status_code == 200, taken.text
    version = taken.json()["conversation"]["inbox_version"]
    reply = await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/reply"),
        {"request_key": str(uuid4()), "expected_version": version, "body": "Yes, it left today."},
    )
    assert reply.status_code == 202, reply.text
    message_id = reply.json()["message_id"]
    queued = await w.q(
        "SELECT endpoint_id, status FROM outbound_deliveries WHERE message_id=%s", message_id
    )
    assert queued == [{"endpoint_id": endpoint, "status": "pending"}]

    # Once the number is disconnected there is no route; the refusal names no channel.
    assert (await w.mutate(w.admin, url(w), method="DELETE")).status_code == 204
    version = reply.json()["conversation"]["inbox_version"]
    refused = await w.mutate(
        w.admin,
        path(w, f"/conversations/{conversation}/reply"),
        {"request_key": str(uuid4()), "expected_version": version, "body": "Still there?"},
    )
    assert refused.status_code == 409
    assert refused.json()["detail"] == "This conversation has no active channel to reply on."


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
