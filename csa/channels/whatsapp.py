"""WhatsApp Cloud API webhook verification, normalization and text delivery."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ValidationError

from csa.contracts.envelope import Attachment, ContractModel, Message
from csa.core.pii import redact

SERVICE_WINDOW = timedelta(hours=24)

_MEDIA_KINDS: dict[str, Literal["image", "audio", "video", "document"]] = {
    "image": "image",
    "audio": "audio",
    "video": "video",
    "document": "document",
    "sticker": "image",
}
_STATUS_VALUES: dict[str, Literal["sent", "delivered", "read", "failed"]] = {
    "sent": "sent",
    "delivered": "delivered",
    "read": "read",
    "failed": "failed",
}


class WhatsAppParseError(ValueError):
    """Raised when a webhook payload is structurally invalid (never echoes the payload)."""


class WhatsAppDeliveryError(Exception):
    """A Graph API send failure with a secret-free error code."""

    def __init__(
        self,
        error_code: str,
        *,
        status_code: int | None,
        retry_after_seconds: int | None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class WhatsAppStatus(ContractModel):
    provider_message_id: str
    status: Literal["sent", "delivered", "read", "failed"]
    recipient_wa_id: str
    occurred_at: datetime
    error_code: str | None = None


class WhatsAppEvent(ContractModel):
    kind: Literal["message", "status", "unsupported"]
    phone_number_id: str
    display_phone: str | None
    wa_id: str  # sender for messages, recipient for statuses
    profile_name: str | None
    provider_message_id: str  # also the idempotency key
    received_at: datetime
    message: Message | None
    status: WhatsAppStatus | None
    unsupported_type: str | None

    @property
    def channel_thread(self) -> str:
        return self.wa_id


class WhatsAppDeliveryReceipt(BaseModel):
    status_code: int
    provider_message_id: str


class WhatsAppAdapter:
    """Verify, parse and send WhatsApp Cloud API traffic."""

    channel = "whatsapp"

    @staticmethod
    def verify_signature(app_secret: str, body: bytes, header: str | None) -> bool:
        """Constant-time check of Meta's ``X-Hub-Signature-256`` header."""
        if not header or not header.startswith("sha256="):
            return False
        expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(header[len("sha256=") :].encode(), expected.encode())

    @staticmethod
    def verify_challenge(
        verify_token: str, mode: str | None, token: str | None, challenge: str | None
    ) -> str | None:
        """Return the challenge to echo for a valid subscription handshake, else None."""
        if mode != "subscribe" or not token or not challenge:
            return None
        if not hmac.compare_digest(token.encode(), verify_token.encode()):
            return None
        return challenge

    @staticmethod
    def parse(raw: bytes | str | Mapping[str, Any]) -> list[WhatsAppEvent]:
        """Normalize one webhook delivery into zero or more events."""
        try:
            payload: object = json.loads(raw) if isinstance(raw, (bytes, str)) else raw
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WhatsAppParseError("request body is not valid JSON") from None
        root = _mapping(payload, "payload")
        entries = _list(root.get("entry"), "entry")
        events: list[WhatsAppEvent] = []
        for entry in entries:
            for change in _list(_mapping(entry, "entry").get("changes"), "changes"):
                change_map = _mapping(change, "change")
                if change_map.get("field") != "messages":
                    continue
                value = _mapping(change_map.get("value"), "value")
                events.extend(_parse_value(value))
        return events

    @staticmethod
    async def send_text(
        phone_number_id: str,
        access_token: str,
        *,
        to: str,
        text: str,
        http_client: httpx.AsyncClient,
        base_url: str,
    ) -> WhatsAppDeliveryReceipt:
        """Send a free-form text message; errors carry only a mapped code."""
        url = f"{base_url.rstrip('/')}/{phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        try:
            response = await http_client.post(
                url, json=payload, headers={"Authorization": f"Bearer {access_token}"}
            )
        except httpx.HTTPError:
            raise WhatsAppDeliveryError(
                "provider_unavailable", status_code=None, retry_after_seconds=30
            ) from None

        status = response.status_code
        if not response.is_success:
            raise _delivery_error(response)
        try:
            message_id = response.json()["messages"][0]["id"]
        except (ValueError, KeyError, IndexError, TypeError):
            message_id = None
        if not isinstance(message_id, str) or not message_id:
            raise WhatsAppDeliveryError(
                "provider_rejected", status_code=status, retry_after_seconds=None
            )
        return WhatsAppDeliveryReceipt(status_code=status, provider_message_id=message_id)


def inside_service_window(now: datetime, last_inbound_at: datetime | None) -> bool:
    """True while free-form replies are allowed (24 hours from the last inbound message)."""
    if last_inbound_at is None:
        return False
    return now - last_inbound_at <= SERVICE_WINDOW


def _delivery_error(response: httpx.Response) -> WhatsAppDeliveryError:
    status = response.status_code
    graph_code: int | None = None
    try:
        error = response.json().get("error")
        if isinstance(error, Mapping) and isinstance(error.get("code"), int):
            graph_code = error["code"]
    except (ValueError, AttributeError):
        pass

    if graph_code == 130429:
        return WhatsAppDeliveryError(
            "rate_limited", status_code=status, retry_after_seconds=_retry_after(response)
        )
    if graph_code == 131047:
        return WhatsAppDeliveryError(
            "outside_service_window", status_code=status, retry_after_seconds=None
        )
    if graph_code == 131026:
        return WhatsAppDeliveryError(
            "recipient_not_on_whatsapp", status_code=status, retry_after_seconds=None
        )
    if graph_code == 190 or status == 401:
        return WhatsAppDeliveryError("token_invalid", status_code=status, retry_after_seconds=None)
    if status >= 500:
        return WhatsAppDeliveryError(
            "provider_unavailable", status_code=status, retry_after_seconds=30
        )
    return WhatsAppDeliveryError("provider_rejected", status_code=status, retry_after_seconds=None)


def _retry_after(response: httpx.Response) -> int:
    raw = response.headers.get("Retry-After")
    try:
        seconds = int(raw) if raw is not None else 60
    except ValueError:
        return 60
    return seconds if seconds > 0 else 60


def _parse_value(value: Mapping[str, Any]) -> list[WhatsAppEvent]:
    metadata = _mapping(value.get("metadata"), "metadata")
    phone_number_id = _required_string(metadata, "phone_number_id")
    display_phone = metadata.get("display_phone_number")
    if not isinstance(display_phone, str):
        display_phone = None

    names: dict[str, str] = {}
    contacts = value.get("contacts")
    if isinstance(contacts, list):
        for contact in contacts:
            if not isinstance(contact, Mapping):
                continue
            profile = contact.get("profile")
            wa_id = contact.get("wa_id")
            name = profile.get("name") if isinstance(profile, Mapping) else None
            if isinstance(wa_id, str) and isinstance(name, str) and name.strip():
                names[wa_id] = name

    events: list[WhatsAppEvent] = []
    try:
        for item in _optional_list(value.get("messages"), "messages"):
            events.append(
                _parse_message(_mapping(item, "message"), phone_number_id, display_phone, names)
            )
        for item in _optional_list(value.get("statuses"), "statuses"):
            events.append(_parse_status(_mapping(item, "status"), phone_number_id, display_phone))
    except ValidationError:
        raise WhatsAppParseError("webhook event is outside the supported contract") from None
    return events


def _parse_message(
    item: Mapping[str, Any],
    phone_number_id: str,
    display_phone: str | None,
    names: Mapping[str, str],
) -> WhatsAppEvent:
    wa_id = _required_string(item, "from")
    message_id = _required_string(item, "id")
    received_at = _timestamp(item.get("timestamp"))
    message_type = _required_string(item, "type")

    message: Message | None = None
    unsupported_type: str | None = None
    if message_type == "text":
        body = _mapping(item.get("text"), "text").get("body")
        if not isinstance(body, str):
            raise WhatsAppParseError("text body is missing or invalid")
        message = Message(body_raw=body, body_redacted=redact(body), language="und")
    else:
        attachment = _media_attachment(item, message_type)
        if attachment is None:
            unsupported_type = message_type
        else:
            message = Message(
                body_raw="", body_redacted="", language="und", attachments=[attachment]
            )

    return WhatsAppEvent(
        kind="message" if message is not None else "unsupported",
        phone_number_id=phone_number_id,
        display_phone=display_phone,
        wa_id=wa_id,
        profile_name=names.get(wa_id),
        provider_message_id=message_id,
        received_at=received_at,
        message=message,
        status=None,
        unsupported_type=unsupported_type,
    )


def _media_attachment(item: Mapping[str, Any], message_type: str) -> Attachment | None:
    kind = _MEDIA_KINDS.get(message_type)
    if kind is None:
        return None
    media = item.get(message_type)
    if not isinstance(media, Mapping):
        return None
    media_id = media.get("id")
    if not isinstance(media_id, str) or not media_id:
        return None
    mime_type = media.get("mime_type")
    if not isinstance(mime_type, str) or not mime_type:
        mime_type = "application/octet-stream"
    filename = media.get("filename")
    return Attachment(
        kind=kind,
        mime_type=mime_type,
        file_id=media_id,
        filename=filename if isinstance(filename, str) else None,
    )


def _parse_status(
    item: Mapping[str, Any], phone_number_id: str, display_phone: str | None
) -> WhatsAppEvent:
    message_id = _required_string(item, "id")
    status_value = item.get("status")
    if not isinstance(status_value, str) or status_value not in _STATUS_VALUES:
        raise WhatsAppParseError("status value is not supported")
    recipient = _required_string(item, "recipient_id")
    occurred_at = _timestamp(item.get("timestamp"))
    error_code: str | None = None
    errors = item.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], Mapping):
        code = errors[0].get("code")
        if isinstance(code, (int, str)) and not isinstance(code, bool):
            error_code = str(code)
    status = WhatsAppStatus(
        provider_message_id=message_id,
        status=_STATUS_VALUES[status_value],
        recipient_wa_id=recipient,
        occurred_at=occurred_at,
        error_code=error_code,
    )
    return WhatsAppEvent(
        kind="status",
        phone_number_id=phone_number_id,
        display_phone=display_phone,
        wa_id=recipient,
        profile_name=None,
        provider_message_id=message_id,
        received_at=occurred_at,
        message=None,
        status=status,
        unsupported_type=None,
    )


def _timestamp(value: object) -> datetime:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, OverflowError, OSError):
        raise WhatsAppParseError("timestamp is missing or invalid") from None


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise WhatsAppParseError(f"{field} must be an object")
    return value


def _list(value: object, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise WhatsAppParseError(f"{field} must be a list")
    return value


def _optional_list(value: object, field: str) -> list[Any]:
    return [] if value is None else _list(value, field)


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise WhatsAppParseError(f"{key} is missing or invalid")
    return result
