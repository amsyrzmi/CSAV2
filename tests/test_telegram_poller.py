"""Telegram long-polling transport and durable-offset tests."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from apps.telegram_poller.main import TelegramPoller, _local_polling_dsn_is_safe
from csa.channels.ingress import IngressReceipt
from csa.channels.telegram import TelegramAdapter, TelegramPollingError


@pytest.mark.asyncio
async def test_telegram_poller_deletes_webhook_without_dropping_updates_and_polls() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/deleteWebhook"):
            return httpx.Response(200, json={"ok": True, "result": True})
        return httpx.Response(200, json={"ok": True, "result": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await TelegramAdapter.delete_webhook("dev-token", http_client=client)
        assert (
            await TelegramAdapter.get_updates(
                "dev-token",
                offset=None,
                http_client=client,
            )
            == []
        )

    delete_payload = json.loads(requests[0].content)
    poll_payload = json.loads(requests[1].content)
    assert delete_payload == {"drop_pending_updates": False}
    assert poll_payload == {
        "timeout": 25,
        "allowed_updates": ["message", "edited_message", "callback_query"],
    }
    assert requests[0].url.path.endswith("/deleteWebhook")
    assert requests[1].url.path.endswith("/getUpdates")
    assert "dev-token" in requests[0].url.path


@pytest.mark.asyncio
async def test_poller_only_advances_offset_after_durable_ingress() -> None:
    api_offsets: list[int | None] = []
    updates = [
        {"update_id": 10, "message": {"text": "first"}},
        {"update_id": 11, "message": {"text": "second"}},
    ]

    def respond(request: httpx.Request) -> httpx.Response:
        api_offsets.append(json.loads(request.content).get("offset"))
        return httpx.Response(200, json={"ok": True, "result": updates})

    class FakeIngress:
        def __init__(self) -> None:
            self.calls: list[int] = []
            self.fail_once = True

        async def receive(
            self,
            public_id: str,
            supplied_secret: str | None,
            body: bytes,
        ) -> IngressReceipt:
            update_id = json.loads(body)["update_id"]
            self.calls.append(update_id)
            if update_id == 11 and self.fail_once:
                self.fail_once = False
                raise RuntimeError("dev database temporarily unavailable")
            return IngressReceipt(accepted=True, duplicate=False, trace_id=uuid.uuid4())

    ingress = FakeIngress()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        poller = TelegramPoller("dev-token", "dev-secret", "endpoint-id", ingress, client)
        with pytest.raises(RuntimeError, match="temporarily unavailable"):
            await poller.poll_once()
        assert await poller.poll_once() == 1

    assert api_offsets == [None, 11]
    assert ingress.calls == [10, 11, 11]


@pytest.mark.asyncio
async def test_poller_rejects_out_of_order_update_batches_before_persisting() -> None:
    def respond(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "ok": True,
                "result": [
                    {"update_id": 12, "message": {"text": "later"}},
                    {"update_id": 11, "message": {"text": "earlier"}},
                ],
            },
        )

    class FakeIngress:
        calls = 0

        async def receive(
            self,
            public_id: str,
            supplied_secret: str | None,
            body: bytes,
        ) -> IngressReceipt:
            self.calls += 1
            return IngressReceipt(accepted=True, duplicate=False, trace_id=uuid.uuid4())

    ingress = FakeIngress()
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        poller = TelegramPoller("dev-token", "dev-secret", "endpoint-id", ingress, client)
        with pytest.raises(TelegramPollingError, match="out_of_order"):
            await poller.poll_once()
    assert ingress.calls == 0


def test_polling_database_guard_requires_isolated_dev_postgres() -> None:
    assert _local_polling_dsn_is_safe("postgresql://local:local@127.0.0.1:5434/csa")
    assert not _local_polling_dsn_is_safe("postgresql://local:local@127.0.0.1:5433/csa")
    assert not _local_polling_dsn_is_safe("postgresql://local:local@db:5434/csa")
