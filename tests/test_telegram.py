"""Telegram adapter normalization and authentication tests (LG03)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from csa.channels.telegram import TelegramAdapter, TelegramDeliveryError, TelegramUpdateError
from csa.contracts.callbacks import CallbackCodec


def _message_update(**message_fields: object) -> dict[str, object]:
    return {
        "update_id": 42,
        "message": {
            "message_id": 7,
            "date": 1791676800,
            "from": {"id": 12345, "first_name": "Test", "last_name": "Customer"},
            "chat": {"id": 12345, "type": "private"},
            **message_fields,
        },
    }


def test_webhook_secret_verification() -> None:
    assert TelegramAdapter.verify("dev-secret", "dev-secret")
    assert not TelegramAdapter.verify(None, "dev-secret")
    assert not TelegramAdapter.verify("wrong-secret", "dev-secret")


def test_text_update_normalizes_and_redacts_personal_data() -> None:
    event = TelegramAdapter.parse(
        _message_update(text="Email test@example.com or call +60123456789"),
        public_id="devbotendpoint0001",
    )

    assert event.kind == "message"
    assert event.sender_id == "12345"
    assert event.channel_thread == "12345"
    assert event.idempotency_key == "telegram:devbotendpoint0001:42"
    assert event.message.body_raw == "Email test@example.com or call +60123456789"
    assert event.message.body_redacted == "Email [EMAIL_0] or call [PHONE_0]"
    assert event.message.language == "und"
    assert event.callback is None


def test_photo_update_uses_largest_file_reference_and_caption() -> None:
    event = TelegramAdapter.parse(
        _message_update(
            caption="Please check this",
            photo=[
                {"file_id": "small-file", "width": 20, "height": 20},
                {"file_id": "large-file", "width": 100, "height": 100},
            ],
        ),
        public_id="devbotendpoint0001",
    )

    assert event.message.body_raw == "Please check this"
    assert len(event.message.attachments) == 1
    assert event.message.attachments[0].kind == "image"
    assert event.message.attachments[0].file_id == "large-file"


def test_callback_update_accepts_only_compact_codec_ids() -> None:
    action_id = uuid.uuid4()
    update = {
        "update_id": 99,
        "callback_query": {
            "id": "query-99",
            "from": {"id": 12345, "first_name": "Test"},
            "message": {
                "date": 1791676800,
                "chat": {"id": 12345, "type": "private"},
            },
            "data": CallbackCodec.encode(action_id),
        },
    }

    event = TelegramAdapter.parse(update, public_id="devbotendpoint0001")

    assert event.kind == "callback_query"
    assert event.callback_query_id == "query-99"
    assert event.callback is not None
    assert event.callback.action_id == action_id
    assert event.message.body_raw == ""

    update["callback_query"]["data"] = "ord:confirm:legacy"  # type: ignore[index]
    with pytest.raises(TelegramUpdateError, match="compact action id"):
        TelegramAdapter.parse(update, public_id="devbotendpoint0001")


@pytest.mark.parametrize(
    "update",
    [
        {"update_id": "not-an-int", "message": {}},
        {"update_id": 1, "message": {"chat": {"id": 1, "type": "group"}}},
        {"update_id": 1, "channel_post": {}},
    ],
)
def test_malformed_or_unsupported_updates_are_rejected(update: dict[str, object]) -> None:
    with pytest.raises(TelegramUpdateError):
        TelegramAdapter.parse(update, public_id="devbotendpoint0001")


@pytest.mark.asyncio
async def test_send_message_posts_plain_text_and_returns_delivery_receipt() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 81}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        receipt = await TelegramAdapter.send_message(
            "12345:test-token",
            chat_id="12345",
            text="Echo: <not markup>",
            http_client=client,
        )

    assert receipt.message_id == 81
    assert receipt.status_code == 200
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/sendMessage")
    assert requests[0].read() == b'{"chat_id":"12345","text":"Echo: <not markup>"}'


@pytest.mark.asyncio
async def test_send_message_exposes_rate_limit_without_response_secrets() -> None:
    def respond(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "parameters": {"retry_after": 7},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(TelegramDeliveryError) as error:
            await TelegramAdapter.send_message(
                "12345:test-token",
                chat_id="12345",
                text="hello",
                http_client=client,
            )

    assert error.value.error_code == "telegram_rate_limited"
    assert error.value.status_code == 429
    assert error.value.retry_after_seconds == 7


def test_staff_callback_is_allowed_from_group_chat_but_customer_callback_is_not() -> None:
    from csa.contracts.callbacks import StaffCallbackCodec

    action_id = uuid.uuid4()

    def update(data: str) -> dict[str, object]:
        return {
            "update_id": 5,
            "callback_query": {
                "id": "q",
                "from": {"id": 7, "first_name": "Staff"},
                "message": {"date": 1791676800, "chat": {"id": -100, "type": "supergroup"}},
                "data": data,
            },
        }

    event = TelegramAdapter.parse(update(StaffCallbackCodec.encode(action_id)), public_id="p")
    assert event.staff_action_id == action_id
    with pytest.raises(TelegramUpdateError, match="private"):
        TelegramAdapter.parse(update(CallbackCodec.encode(action_id)), public_id="p")
