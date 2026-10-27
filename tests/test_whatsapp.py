"""WhatsApp Cloud API adapter, settings and service-window rule (no database)."""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from csa.channels.whatsapp import (
    SERVICE_WINDOW,
    WhatsAppAdapter,
    WhatsAppDeliveryError,
    WhatsAppParseError,
    inside_service_window,
)
from csa.contracts.envelope import Attachment
from csa.core.settings import Settings
from pydantic import ValidationError

from tests.test_console_identity_config import configured

PHONE_NUMBER_ID = "106540352242922"
BASE_URL = "https://graph.example.test/v99.0"


def _value(**extra: Any) -> dict[str, Any]:
    return {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15551234567", "phone_number_id": PHONE_NUMBER_ID},
        **extra,
    }


def _webhook(*values: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {"id": f"WABA{i}", "changes": [{"field": "messages", "value": value}]}
            for i, value in enumerate(values)
        ],
    }


TEXT_VALUE = _value(
    contacts=[{"profile": {"name": "Aisyah"}, "wa_id": "60123456789"}],
    messages=[
        {
            "from": "60123456789",
            "id": "wamid.HBgLNjAxMjM0NTY3ODkVAgASGBQz",
            "timestamp": "1761523200",
            "type": "text",
            "text": {"body": "Hello"},
        }
    ],
)
TEXT_WEBHOOK = _webhook(TEXT_VALUE)


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_signature_verifies_only_matching_hmac() -> None:
    body = json.dumps(TEXT_WEBHOOK).encode()
    header = _sign("app-secret", body)
    assert WhatsAppAdapter.verify_signature("app-secret", body, header) is True
    assert WhatsAppAdapter.verify_signature("other-secret", body, header) is False
    assert WhatsAppAdapter.verify_signature("app-secret", body, None) is False
    assert WhatsAppAdapter.verify_signature("app-secret", body, "sha1=" + header[7:]) is False


def test_challenge_echoes_only_for_matching_token() -> None:
    assert WhatsAppAdapter.verify_challenge("tok", "subscribe", "tok", "12345") == "12345"
    assert WhatsAppAdapter.verify_challenge("tok", "subscribe", "bad", "12345") is None
    assert WhatsAppAdapter.verify_challenge("tok", "unsubscribe", "tok", "12345") is None
    assert WhatsAppAdapter.verify_challenge("tok", "subscribe", None, "12345") is None
    assert WhatsAppAdapter.verify_challenge("tok", "subscribe", "tok", None) is None


def test_parse_text_message_yields_message_event() -> None:
    events = WhatsAppAdapter.parse(json.dumps(TEXT_WEBHOOK).encode())
    assert len(events) == 1
    event = events[0]
    assert event.kind == "message"
    assert event.phone_number_id == PHONE_NUMBER_ID
    assert event.display_phone == "15551234567"
    assert event.wa_id == "60123456789"
    assert event.channel_thread == "60123456789"
    assert event.profile_name == "Aisyah"
    assert event.provider_message_id == "wamid.HBgLNjAxMjM0NTY3ODkVAgASGBQz"
    assert event.received_at == datetime.fromtimestamp(1761523200, tz=UTC)
    assert event.message is not None
    assert event.message.body_raw == "Hello"
    assert event.message.body_redacted == "Hello"
    assert event.message.attachments == []
    assert event.status is None
    assert event.unsupported_type is None


def test_parse_image_yields_attachment_stub() -> None:
    payload = _webhook(
        _value(
            messages=[
                {
                    "from": "60123456789",
                    "id": "wamid.IMG",
                    "timestamp": "1761523201",
                    "type": "image",
                    "image": {"id": "MEDIA1", "mime_type": "image/jpeg", "sha256": "x"},
                },
                {
                    "from": "60123456789",
                    "id": "wamid.LOC",
                    "timestamp": "1761523202",
                    "type": "location",
                    "location": {"latitude": 1.0, "longitude": 2.0},
                },
            ]
        )
    )
    image, location = WhatsAppAdapter.parse(payload)
    assert image.kind == "message"
    assert image.message is not None
    assert image.message.body_raw == ""
    assert image.message.attachments == [
        Attachment(kind="image", mime_type="image/jpeg", file_id="MEDIA1")
    ]
    assert location.kind == "unsupported"
    assert location.unsupported_type == "location"
    assert location.message is None
    assert location.profile_name is None


def test_parse_statuses_yield_status_events() -> None:
    payload = _webhook(
        _value(
            statuses=[
                {
                    "id": "wamid.OUT",
                    "status": "delivered",
                    "timestamp": "1761523300",
                    "recipient_id": "60123456789",
                },
                {
                    "id": "wamid.OUT",
                    "status": "read",
                    "timestamp": "1761523400",
                    "recipient_id": "60123456789",
                },
                {
                    "id": "wamid.BAD",
                    "status": "failed",
                    "timestamp": "1761523500",
                    "recipient_id": "60123456789",
                    "errors": [{"code": 131047, "title": "Re-engagement"}],
                },
            ]
        )
    )
    events = WhatsAppAdapter.parse(payload)
    assert [e.kind for e in events] == ["status", "status", "status"]
    assert [e.status.status for e in events if e.status] == ["delivered", "read", "failed"]
    first, _, failed = events
    assert first.provider_message_id == "wamid.OUT"
    assert first.wa_id == "60123456789"
    assert first.message is None
    assert first.status is not None
    assert first.status.occurred_at == datetime.fromtimestamp(1761523300, tz=UTC)
    assert first.status.error_code is None
    assert failed.status is not None
    assert failed.status.error_code == "131047"


def test_parse_two_entries_two_numbers() -> None:
    second = copy.deepcopy(TEXT_VALUE)
    second["metadata"] = {"display_phone_number": "15559876543", "phone_number_id": "999"}
    second["messages"][0]["id"] = "wamid.SECOND"
    events = WhatsAppAdapter.parse(_webhook(TEXT_VALUE, second))
    assert [e.phone_number_id for e in events] == [PHONE_NUMBER_ID, "999"]
    assert [e.display_phone for e in events] == ["15551234567", "15559876543"]
    assert events[1].provider_message_id == "wamid.SECOND"


def test_parse_rejects_malformed_payload() -> None:
    with pytest.raises(WhatsAppParseError):
        WhatsAppAdapter.parse({"object": "whatsapp_business_account"})
    with pytest.raises(WhatsAppParseError):
        WhatsAppAdapter.parse(b"not json")
    bad = copy.deepcopy(TEXT_WEBHOOK)
    del bad["entry"][0]["changes"][0]["value"]["messages"][0]["text"]
    with pytest.raises(WhatsAppParseError) as excinfo:
        WhatsAppAdapter.parse(bad)
    assert "60123456789" not in str(excinfo.value)


def test_service_window_boundaries() -> None:
    now = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)
    assert timedelta(hours=24) == SERVICE_WINDOW
    assert inside_service_window(now, None) is False
    assert inside_service_window(now, now - timedelta(hours=24)) is True
    assert inside_service_window(now, now - timedelta(hours=24, seconds=1)) is False


async def test_send_text_posts_bearer_json_and_returns_message_id() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.OUT"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        receipt = await WhatsAppAdapter.send_text(
            PHONE_NUMBER_ID,
            "tok-123",
            to="60123456789",
            text="Hi there",
            http_client=client,
            base_url=BASE_URL + "/",
        )
    assert receipt.provider_message_id == "wamid.OUT"
    assert receipt.status_code == 200
    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == f"{BASE_URL}/{PHONE_NUMBER_ID}/messages"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert json.loads(request.content) == {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": "60123456789",
        "type": "text",
        "text": {"preview_url": False, "body": "Hi there"},
    }


@pytest.mark.parametrize(
    ("status", "code", "expected", "retry"),
    [
        (429, 130429, "rate_limited", 60),
        (400, 131047, "outside_service_window", None),
        (400, 131026, "recipient_not_on_whatsapp", None),
        (401, 190, "token_invalid", None),
        (503, None, "provider_unavailable", 30),
        (400, 100, "provider_rejected", None),
    ],
)
async def test_send_text_maps_graph_errors(
    status: int, code: int | None, expected: str, retry: int | None
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = {"error": {"code": code, "message": "secret detail"}} if code else {}
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WhatsAppDeliveryError) as excinfo:
            await WhatsAppAdapter.send_text(
                PHONE_NUMBER_ID, "tok", to="6012", text="x", http_client=client, base_url=BASE_URL
            )
    assert excinfo.value.error_code == expected
    assert excinfo.value.status_code == status
    assert excinfo.value.retry_after_seconds == retry
    assert "secret detail" not in str(excinfo.value)


async def test_send_text_honours_retry_after_and_network_errors() -> None:
    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "17"}, json={"error": {"code": 130429}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as client:
        with pytest.raises(WhatsAppDeliveryError) as excinfo:
            await WhatsAppAdapter.send_text(
                PHONE_NUMBER_ID, "t", to="6", text="x", http_client=client, base_url=BASE_URL
            )
    assert excinfo.value.retry_after_seconds == 17

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(WhatsAppDeliveryError) as excinfo:
            await WhatsAppAdapter.send_text(
                PHONE_NUMBER_ID, "t", to="6", text="x", http_client=client, base_url=BASE_URL
            )
    assert excinfo.value.error_code == "provider_unavailable"
    assert excinfo.value.status_code is None
    assert excinfo.value.retry_after_seconds == 30


def test_settings_require_both_whatsapp_secrets_together() -> None:
    with pytest.raises(ValidationError):
        configured(WHATSAPP_APP_SECRET="app")
    with pytest.raises(ValidationError):
        configured(WHATSAPP_VERIFY_TOKEN="verify")
    both: Settings = configured(WHATSAPP_APP_SECRET="app", WHATSAPP_VERIFY_TOKEN="verify")
    assert both.whatsapp_enabled is True
    assert configured().whatsapp_enabled is False
    assert str(configured().WHATSAPP_GRAPH_BASE_URL).startswith("https://graph.facebook.com/")
