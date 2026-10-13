"""Telegram webhook verification and update normalization (LG03 §3A)."""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from csa.contracts.callbacks import CallbackCodec, StaffCallbackCodec
from csa.contracts.envelope import Attachment, Callback, ContractModel, Message
from csa.core.pii import redact


class TelegramUpdateError(ValueError):
    """Raised when a Telegram update is malformed or cannot be normalized."""


class TelegramDeliveryError(Exception):
    """A Telegram Bot API send failure with a secret-free error code."""

    def __init__(
        self,
        error_code: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class _TelegramSendMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message_id: int


class _TelegramResponseParameters(BaseModel):
    model_config = ConfigDict(extra="ignore")

    retry_after: int | None = None


class _TelegramSendResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: _TelegramSendMessage | None = None
    parameters: _TelegramResponseParameters | None = None


class _TelegramUpdatesResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: list[dict[str, Any]] | None = None


class _TelegramBooleanResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool
    result: bool | None = None


class TelegramDeliveryReceipt(BaseModel):
    """The non-secret receipt persisted by the delivery worker."""

    message_id: int
    status_code: int


class TelegramPollingError(Exception):
    """A secret-free Telegram Bot API polling failure."""

    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


class TelegramEvent(ContractModel):
    update_id: int = Field(ge=0)
    kind: Literal["message", "edited_message", "callback_query"]
    sender_id: str = Field(min_length=1, max_length=200)
    chat_id: str = Field(min_length=1, max_length=200)
    display_name: str | None = None
    received_at: datetime
    idempotency_key: str = Field(min_length=1, max_length=512)
    message: Message
    callback: Callback | None = None
    callback_query_id: str | None = None
    source_message_id: int | None = None
    staff_action_id: UUID | None = None

    @property
    def channel_thread(self) -> str:
        return self.chat_id


class TelegramAdapter:
    """Parse Telegram private-chat text, media, and compact callback updates."""

    channel = "telegram"

    @staticmethod
    async def delete_webhook(
        bot_token: str,
        *,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Switch the bot to polling without discarding queued updates."""
        response = await _telegram_api_post(
            bot_token,
            "deleteWebhook",
            {"drop_pending_updates": False},
            http_client=http_client,
        )
        try:
            body = _TelegramBooleanResponse.model_validate(response)
        except ValidationError as exc:
            raise TelegramPollingError("telegram_invalid_response") from exc
        if not body.ok or body.result is not True:
            raise TelegramPollingError("telegram_api_rejected")

    @staticmethod
    async def get_updates(
        bot_token: str,
        *,
        offset: int | None,
        http_client: httpx.AsyncClient | None = None,
    ) -> list[dict[str, Any]]:
        """Long-poll the bot for supported update types."""
        payload: dict[str, Any] = {
            "timeout": 25,
            "allowed_updates": ["message", "edited_message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        response = await _telegram_api_post(
            bot_token,
            "getUpdates",
            payload,
            http_client=http_client,
        )
        try:
            body = _TelegramUpdatesResponse.model_validate(response)
        except ValidationError as exc:
            raise TelegramPollingError("telegram_invalid_response") from exc
        if not body.ok or body.result is None:
            raise TelegramPollingError("telegram_api_rejected")
        return body.result

    @staticmethod
    async def send_message(
        bot_token: str,
        *,
        chat_id: str,
        text: str,
        http_client: httpx.AsyncClient | None = None,
        buttons: Sequence[tuple[str, str]] | None = None,
    ) -> TelegramDeliveryReceipt:
        """Send text (optionally with one row of inline buttons) and return the message id.

        Error values intentionally exclude the Bot API URL because it contains
        the credential token.
        """
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": label, "callback_data": data}
                        for label, data in buttons[start : start + 2]
                    ]
                    for start in range(0, len(buttons), 2)
                ]
            }
        try:
            if http_client is None:
                timeout = httpx.Timeout(connect=3, read=10, write=10, pool=3)
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(url, json=payload)
            else:
                response = await http_client.post(url, json=payload)
        except httpx.TimeoutException as exc:
            raise TelegramDeliveryError("telegram_timeout") from exc
        except httpx.HTTPError as exc:
            raise TelegramDeliveryError("telegram_transport_error") from exc

        try:
            body = _TelegramSendResponse.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise TelegramDeliveryError(
                "telegram_invalid_response",
                status_code=response.status_code,
            ) from exc

        if response.status_code == 429:
            raise TelegramDeliveryError(
                "telegram_rate_limited",
                status_code=response.status_code,
                retry_after_seconds=(
                    body.parameters.retry_after if body.parameters is not None else None
                ),
            )
        if not response.is_success:
            raise TelegramDeliveryError(
                f"telegram_http_{response.status_code}",
                status_code=response.status_code,
            )
        if not body.ok or body.result is None:
            raise TelegramDeliveryError(
                "telegram_api_rejected",
                status_code=response.status_code,
            )
        return TelegramDeliveryReceipt(
            message_id=body.result.message_id,
            status_code=response.status_code,
        )

    @staticmethod
    def verify(supplied_secret: str | None, expected_secret: str) -> bool:
        """Constant-time compare Telegram's configured webhook secret token."""
        return bool(supplied_secret) and hmac.compare_digest(supplied_secret, expected_secret)

    @staticmethod
    def parse(raw_update: bytes | str | Mapping[str, Any], *, public_id: str) -> TelegramEvent:
        """Validate and normalize one Telegram Update into a typed event."""
        try:
            if isinstance(raw_update, bytes):
                update: object = json.loads(raw_update)
            elif isinstance(raw_update, str):
                update = json.loads(raw_update)
            else:
                update = raw_update
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TelegramUpdateError("request body is not valid JSON") from exc

        if not isinstance(update, Mapping):
            raise TelegramUpdateError("Telegram update must be a JSON object")

        try:
            update_id = int(update["update_id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TelegramUpdateError("Telegram update_id is missing or invalid") from exc

        kind: Literal["message", "edited_message", "callback_query"]
        callback_query_id: str | None = None
        callback: Callback | None = None
        staff_action_id: UUID | None = None
        source_message_id: int | None = None
        if "callback_query" in update:
            kind = "callback_query"
            query = _mapping(update["callback_query"], "callback_query")
            callback_query_id = _required_string(query, "id")
            sender = _mapping(query.get("from"), "callback_query.from")
            message_data = _mapping(query.get("message"), "callback_query.message")
            raw_message_id = message_data.get("message_id")
            if isinstance(raw_message_id, int) and not isinstance(raw_message_id, bool):
                source_message_id = raw_message_id
            callback_data = query.get("data")
            if not isinstance(callback_data, str):
                raise TelegramUpdateError("callback data is missing")
            staff_id = StaffCallbackCodec.decode(callback_data)
            if staff_id is not None:
                staff_action_id = staff_id
            else:
                action_id = CallbackCodec.decode(callback_data)
                if action_id is None:
                    raise TelegramUpdateError("callback data is not a valid compact action id")
                callback = Callback(action_id=action_id)
        elif "message" in update or "edited_message" in update:
            key = "edited_message" if "edited_message" in update else "message"
            kind = "edited_message" if key == "edited_message" else "message"
            message_data = _mapping(update[key], key)
            sender = _mapping(message_data.get("from"), f"{key}.from")
        else:
            raise TelegramUpdateError("Telegram update type is not supported")

        chat = _mapping(message_data.get("chat"), f"{kind}.chat")
        if staff_action_id is None and chat.get("type") != "private":
            raise TelegramUpdateError("only private Telegram chats are enabled")
        sender_id = _telegram_id(sender.get("id"), "sender id")
        chat_id = _telegram_id(
            chat.get("id"), "chat id", allow_negative=staff_action_id is not None
        )
        timestamp = message_data.get("date")
        if timestamp is None:
            received_at = datetime.now(UTC)
        else:
            try:
                received_at = datetime.fromtimestamp(int(timestamp), tz=UTC)
            except (TypeError, ValueError, OverflowError, OSError) as exc:
                raise TelegramUpdateError("Telegram message date is invalid") from exc

        first_name = sender.get("first_name")
        last_name = sender.get("last_name")
        display_name = " ".join(
            value for value in (first_name, last_name) if isinstance(value, str) and value.strip()
        )

        body_value = message_data.get("text", message_data.get("caption", ""))
        if not isinstance(body_value, str):
            raise TelegramUpdateError("Telegram message text or caption must be a string")
        attachments = _attachments(message_data)
        try:
            message = Message(
                body_raw=body_value,
                body_redacted=redact(body_value),
                language="und",
                attachments=attachments,
            )
            return TelegramEvent(
                update_id=update_id,
                kind=kind,
                sender_id=sender_id,
                chat_id=chat_id,
                display_name=display_name or None,
                received_at=received_at,
                idempotency_key=f"telegram:{public_id}:{update_id}",
                message=message,
                callback=callback,
                callback_query_id=callback_query_id,
                source_message_id=source_message_id,
                staff_action_id=staff_action_id,
            )
        except ValidationError as exc:
            raise TelegramUpdateError("Telegram update is outside the supported contract") from exc


async def _telegram_api_post(
    bot_token: str,
    method: str,
    payload: dict[str, Any],
    *,
    http_client: httpx.AsyncClient | None,
) -> object:
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    try:
        if http_client is None:
            timeout = httpx.Timeout(connect=3, read=35, write=10, pool=3)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(url, json=payload)
        else:
            response = await http_client.post(url, json=payload)
    except httpx.TimeoutException:
        raise TelegramPollingError("telegram_timeout") from None
    except httpx.HTTPError:
        raise TelegramPollingError("telegram_transport_error") from None

    try:
        body: object = response.json()
    except ValueError:
        raise TelegramPollingError("telegram_invalid_response") from None
    if not response.is_success:
        raise TelegramPollingError(f"telegram_http_{response.status_code}")
    return body


def _mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TelegramUpdateError(f"{field} must be an object")
    return value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise TelegramUpdateError(f"{key} is missing or invalid")
    return result


def _telegram_id(value: object, field: str, *, allow_negative: bool = False) -> str:
    if isinstance(value, bool):
        raise TelegramUpdateError(f"{field} is invalid")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = int(value)
        except ValueError as exc:
            raise TelegramUpdateError(f"{field} is invalid") from exc
    else:
        raise TelegramUpdateError(f"{field} is invalid")
    if parsed == 0 or (parsed < 0 and not allow_negative):
        raise TelegramUpdateError(f"{field} is invalid")
    return str(parsed)


def _attachments(message: Mapping[str, Any]) -> list[Attachment]:
    if "photo" in message:
        photos = message["photo"]
        if not isinstance(photos, list) or not photos:
            raise TelegramUpdateError("photo must contain at least one size")
        photo = _mapping(photos[-1], "photo")
        return [
            Attachment(
                kind="image", mime_type="image/jpeg", file_id=_required_string(photo, "file_id")
            )
        ]

    attachment_specs: tuple[tuple[str, Literal["document", "audio", "video"], str], ...] = (
        ("document", "document", "application/octet-stream"),
        ("audio", "audio", "audio/mpeg"),
        ("voice", "audio", "audio/ogg"),
        ("video", "video", "video/mp4"),
    )
    for key, kind, fallback_mime in attachment_specs:
        if key in message:
            item = _mapping(message[key], key)
            file_id = _required_string(item, "file_id")
            mime_type = item.get("mime_type", fallback_mime)
            if not isinstance(mime_type, str) or not mime_type:
                mime_type = fallback_mime
            filename = item.get("file_name")
            size_bytes = item.get("file_size")
            return [
                Attachment(
                    kind=kind,
                    mime_type=mime_type,
                    file_id=file_id,
                    filename=filename if isinstance(filename, str) else None,
                    size_bytes=size_bytes if isinstance(size_bytes, int) else None,
                )
            ]
    return []
