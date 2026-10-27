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
import structlog
from csa.channels.whatsapp import (
    SERVICE_WINDOW,
    WhatsAppAdapter,
    WhatsAppDeliveryError,
    WhatsAppParseError,
    WhatsAppVerifyError,
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
    # Non-media message types reach the coordinator with a type-only stub.
    assert location.kind == "message"
    assert location.unsupported_type is None
    assert location.message is not None
    assert location.message.body_raw == ""
    assert location.message.attachments == [
        Attachment(kind="other", mime_type="application/vnd.whatsapp.location")
    ]
    assert location.profile_name is None


@pytest.mark.parametrize("message_type", ["contacts", "interactive", "button", "order"])
def test_parse_other_message_types_yield_type_only_stub(message_type: str) -> None:
    item = {
        "from": "60123456789",
        "id": f"wamid.{message_type}",
        "timestamp": "1761523202",
        "type": message_type,
        message_type: {"anything": "ignored"},
    }
    (event,) = WhatsAppAdapter.parse(_webhook(_value(messages=[item])))
    assert event.kind == "message"
    assert event.message is not None
    assert event.message.attachments == [
        Attachment(kind="other", mime_type=f"application/vnd.whatsapp.{message_type}")
    ]


def test_media_without_an_id_keeps_its_kind_and_has_no_media_id() -> None:
    item = {
        "from": "60123456789",
        "id": "wamid.NOID",
        "timestamp": "1761523202",
        "type": "audio",
        "audio": {"mime_type": "audio/ogg"},
    }
    (event,) = WhatsAppAdapter.parse(_webhook(_value(messages=[item])))
    assert event.kind == "message"
    assert event.message is not None
    assert event.message.attachments == [Attachment(kind="audio", mime_type="audio/ogg")]


@pytest.mark.parametrize("message_type", ["reaction", "system", "unsupported", "request_welcome"])
def test_non_message_events_stay_inert(message_type: str) -> None:
    item = {
        "from": "60123456789",
        "id": f"wamid.{message_type}",
        "timestamp": "1761523202",
        "type": message_type,
    }
    (event,) = WhatsAppAdapter.parse(_webhook(_value(messages=[item])))
    assert event.kind == "unsupported"
    assert event.unsupported_type == message_type
    assert event.message is None


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


def test_parse_rejects_a_broken_envelope() -> None:
    with pytest.raises(WhatsAppParseError):
        WhatsAppAdapter.parse({"object": "whatsapp_business_account"})
    with pytest.raises(WhatsAppParseError):
        WhatsAppAdapter.parse(b"not json")
    with pytest.raises(WhatsAppParseError):
        WhatsAppAdapter.parse(b"[]")
    no_number = copy.deepcopy(TEXT_WEBHOOK)
    del no_number["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"]
    with pytest.raises(WhatsAppParseError) as excinfo:
        WhatsAppAdapter.parse(no_number)
    assert "60123456789" not in str(excinfo.value)


def test_parse_skips_an_item_it_does_not_understand_and_keeps_the_rest() -> None:
    batch = copy.deepcopy(TEXT_WEBHOOK)
    value = batch["entry"][0]["changes"][0]["value"]
    broken = copy.deepcopy(value["messages"][0])
    broken["id"] = "wamid.BROKEN"
    broken["text"] = {"body": None, "secret": "customer-private-words"}
    value["messages"].insert(0, broken)
    value["messages"].append("not an object")
    value["statuses"] = [
        {"id": "wamid.OUT", "status": "deleted", "timestamp": "1", "recipient_id": "601"},
        {"id": "wamid.OUT", "status": "read", "timestamp": "1", "recipient_id": "601"},
    ]
    with structlog.testing.capture_logs() as logs:
        events = WhatsAppAdapter.parse(batch)
    assert [(e.kind, e.provider_message_id) for e in events] == [
        ("message", "wamid.HBgLNjAxMjM0NTY3ODkVAgASGBQz"),
        ("status", "wamid.OUT"),
    ]
    skipped = [entry for entry in logs if entry["event"] == "whatsapp_item_skipped"]
    assert [entry["item"] for entry in skipped] == ["message", "message", "status"]
    assert all(entry["log_level"] == "warning" for entry in skipped)
    assert all(entry["phone_number_id"] == PHONE_NUMBER_ID for entry in skipped)
    assert "customer-private-words" not in repr(logs)
    assert "Hello" not in repr(logs)


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
        (429, None, "rate_limited", 60),
        (400, 131056, "rate_limited", 60),
        (400, 80007, "rate_limited", 60),
        (400, 4, "rate_limited", 60),
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


async def test_verify_number_reads_the_profile_with_the_supplied_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "display_phone_number": "+60 12-345 6789",
                "verified_name": "Kedai Synthetic",
                "id": PHONE_NUMBER_ID,
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        profile = await WhatsAppAdapter.verify_number(
            PHONE_NUMBER_ID, "tok-123", http_client=client, base_url=BASE_URL + "/"
        )
    assert profile.display_phone == "+60 12-345 6789"
    assert profile.verified_name == "Kedai Synthetic"
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == f"/v99.0/{PHONE_NUMBER_ID}"
    assert request.url.params["fields"] == "display_phone_number,verified_name"
    assert request.headers["Authorization"] == "Bearer tok-123"


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, {"error": {"code": 190}}, "whatsapp_token_rejected"),
        (400, {"error": {"code": 100}}, "whatsapp_token_rejected"),
        (403, {"error": {"code": 10}}, "whatsapp_token_rejected"),
        (404, {}, "whatsapp_token_rejected"),
        (500, {}, "whatsapp_unreachable"),
        (503, {}, "whatsapp_unreachable"),
        (200, {"verified_name": "No number"}, "whatsapp_unreachable"),
    ],
)
async def test_verify_number_maps_failures(status: int, body: dict, expected: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(WhatsAppVerifyError) as excinfo:
            await WhatsAppAdapter.verify_number(
                PHONE_NUMBER_ID, "tok-secret", http_client=client, base_url=BASE_URL
            )
    assert excinfo.value.error_code == expected
    assert "tok-secret" not in str(excinfo.value)


async def test_verify_number_network_failure_is_unreachable() -> None:
    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom tok-secret")

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken)) as client:
        with pytest.raises(WhatsAppVerifyError) as excinfo:
            await WhatsAppAdapter.verify_number(
                PHONE_NUMBER_ID, "tok-secret", http_client=client, base_url=BASE_URL
            )
    assert excinfo.value.error_code == "whatsapp_unreachable"
    assert excinfo.value.__cause__ is None
    assert "tok-secret" not in str(excinfo.value)


def test_settings_require_both_whatsapp_secrets_together() -> None:
    with pytest.raises(ValidationError):
        configured(WHATSAPP_APP_SECRET="app")
    with pytest.raises(ValidationError):
        configured(WHATSAPP_VERIFY_TOKEN="verify")
    both: Settings = configured(WHATSAPP_APP_SECRET="app", WHATSAPP_VERIFY_TOKEN="verify")
    assert both.whatsapp_enabled is True
    assert configured().whatsapp_enabled is False
    assert str(configured().WHATSAPP_GRAPH_BASE_URL).startswith("https://graph.facebook.com/")


@pytest.mark.parametrize("bad_status", [[], {}, 7, None, "unknown", "deleted"])
def test_parse_skips_malformed_or_unknown_status(bad_status: Any) -> None:
    """A non-string or unknown status value is skipped, never a TypeError or a 422."""
    status = {"id": "wamid.OUT", "status": bad_status, "timestamp": "1761523300"}
    status["recipient_id"] = "60123456789"
    assert WhatsAppAdapter.parse(_webhook(_value(statuses=[status]))) == []


def test_disabled_sentinel_leaves_whatsapp_unset() -> None:
    off: Settings = configured(WHATSAPP_APP_SECRET="disabled", WHATSAPP_VERIFY_TOKEN="disabled")
    assert off.WHATSAPP_APP_SECRET is None
    assert off.WHATSAPP_VERIFY_TOKEN is None
    assert off.whatsapp_enabled is False
    # One real value with one placeholder is still the both-or-neither error.
    with pytest.raises(ValueError, match="set together"):
        configured(WHATSAPP_APP_SECRET="app", WHATSAPP_VERIFY_TOKEN="disabled")
    real: Settings = configured(WHATSAPP_APP_SECRET="app", WHATSAPP_VERIFY_TOKEN="verify")
    assert real.whatsapp_enabled is True


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_whatsapp_secrets_are_unset(blank: str) -> None:
    """An empty secret file must never enable a forgeable signature check."""
    off: Settings = configured(WHATSAPP_APP_SECRET=blank, WHATSAPP_VERIFY_TOKEN=blank)
    assert off.WHATSAPP_APP_SECRET is None
    assert off.WHATSAPP_VERIFY_TOKEN is None
    assert off.whatsapp_enabled is False
    with pytest.raises(ValueError, match="set together"):
        configured(WHATSAPP_APP_SECRET=blank, WHATSAPP_VERIFY_TOKEN="verify")
    with pytest.raises(ValueError, match="set together"):
        configured(WHATSAPP_APP_SECRET="app", WHATSAPP_VERIFY_TOKEN=blank)


def test_callback_address_uses_the_hooks_origin_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    from csa.console import channels

    console_only = configured(WHATSAPP_APP_SECRET="app", WHATSAPP_VERIFY_TOKEN="verify")
    monkeypatch.setattr(channels, "get_settings", lambda: console_only)
    assert channels.present(None).callback_url == (
        f"{str(console_only.PUBLIC_BASE_URL).rstrip('/')}/channels/whatsapp"
    )
    hooks = configured(
        WHATSAPP_APP_SECRET="app",
        WHATSAPP_VERIFY_TOKEN="verify",
        WHATSAPP_CALLBACK_BASE_URL="https://hooks.example.test",
    )
    monkeypatch.setattr(channels, "get_settings", lambda: hooks)
    assert channels.present(None).callback_url == "https://hooks.example.test/channels/whatsapp"
