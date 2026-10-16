"""Tenant-resolved durable ingress for the first Telegram adapter (LG03)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.approval.service import EDIT_PROMPT_TEXT
from csa.channels.telegram import (
    TelegramAdapter,
    TelegramDeliveryError,
    TelegramEvent,
    TelegramUpdateError,
)
from csa.contracts.envelope import Channel, Contact, ConversationRef, Envelope, Meta
from csa.core import crypto, db
from csa.core.tenancy import tx
from csa.staff.callbacks import NOT_AUTHORISED_TEXT, handle_staff_action, submit_staff_edit

logger = structlog.get_logger(__name__)

TELEGRAM_WEBHOOK_SECRET_META_KEY = "telegram_webhook_secret_enc"
TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN = "meta.telegram_webhook_secret_enc"
_MAX_UPDATE_BYTES = 1_000_000


class IngressError(Exception):
    """An expected ingress rejection with an HTTP-safe status and message."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class IngressReceipt:
    accepted: bool
    duplicate: bool
    trace_id: UUID
    proxied: bool = False


class EndpointRateLimiter:
    """Small in-process per-endpoint sliding-window limiter."""

    def __init__(self) -> None:
        self._requests: dict[UUID, deque[float]] = {}

    def allow(self, endpoint_id: UUID, *, limit_per_minute: int) -> bool:
        now = time.monotonic()
        events = self._requests.setdefault(endpoint_id, deque())
        while events and now - events[0] >= 60:
            events.popleft()
        if len(events) >= limit_per_minute:
            return False
        events.append(now)
        return True


class _StaffTap(Exception):
    """Internal control flow: a verified staff button tap that bypasses the customer queue."""

    def __init__(
        self,
        *,
        client_id: UUID,
        endpoint_id: UUID,
        trace_id: UUID,
        event: TelegramEvent,
        staff_action_id: UUID,
        bot_token: str,
    ) -> None:
        super().__init__("staff tap")
        self.client_id = client_id
        self.endpoint_id = endpoint_id
        self.trace_id = trace_id
        self.event = event
        self.staff_action_id = staff_action_id
        self.bot_token = bot_token


class _StaffEdit(Exception):
    """Internal control flow: replacement text from a staff chat with an armed Edit."""

    def __init__(
        self,
        *,
        client_id: UUID,
        endpoint_id: UUID,
        trace_id: UUID,
        event: TelegramEvent,
        payload_hash: str,
        bot_token: str,
    ) -> None:
        super().__init__("staff edit")
        self.client_id = client_id
        self.endpoint_id = endpoint_id
        self.trace_id = trace_id
        self.event = event
        self.payload_hash = payload_hash
        self.bot_token = bot_token


class TelegramIngress:
    """Verify, normalize, tenant-bind, and persist Telegram webhook updates."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        adapter: type[TelegramAdapter] = TelegramAdapter,
        rate_limiter: EndpointRateLimiter | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._pool = pool
        self._adapter = adapter
        self._rate_limiter = rate_limiter or EndpointRateLimiter()
        self._http_client = http_client

    async def receive(
        self,
        public_id: str,
        supplied_secret: str | None,
        body: bytes,
    ) -> IngressReceipt:
        """Accept a verified Telegram update or reject it without side effects."""
        try:
            return await self._receive(public_id, supplied_secret, body)
        except _StaffTap as tap:
            answer = await handle_staff_action(
                self._pool,
                client_id=tap.client_id,
                endpoint_id=tap.endpoint_id,
                chat_id=tap.event.chat_id,
                staff_action_id=tap.staff_action_id,
                trace_id=tap.trace_id,
                source_message_id=tap.event.source_message_id,
            )
            if tap.event.callback_query_id:
                await self._answer_callback(tap.bot_token, tap.event.callback_query_id, answer)
            if answer not in (NOT_AUTHORISED_TEXT, EDIT_PROMPT_TEXT):
                await self._clear_buttons(tap.bot_token, tap.event)
            return IngressReceipt(accepted=True, duplicate=False, trace_id=tap.trace_id)
        except _StaffEdit as edit:
            answer = await submit_staff_edit(
                self._pool,
                client_id=edit.client_id,
                chat_id=edit.event.chat_id,
                text=edit.event.message.body_raw,
                reply_to_message_id=edit.event.reply_to_message_id if edit.event.is_group else None,
            )
            # Recording the update makes a Telegram retry a duplicate, not a customer message.
            async with tx(self._pool, str(edit.client_id)) as conn:
                await db.q.fetch_one(
                    "ingress/insert_handled_event.sql",
                    (
                        edit.client_id,
                        edit.endpoint_id,
                        edit.event.idempotency_key,
                        edit.payload_hash,
                        edit.trace_id,
                    ),
                    conn=conn,
                )
            try:
                await self._adapter.send_message(
                    edit.bot_token,
                    chat_id=edit.event.chat_id,
                    text=answer,
                    http_client=self._http_client,
                )
            except TelegramDeliveryError as exc:
                logger.error("staff_edit_reply_failed", error=exc.error_code)
            return IngressReceipt(accepted=True, duplicate=False, trace_id=edit.trace_id)

    async def _receive(
        self,
        public_id: str,
        supplied_secret: str | None,
        body: bytes,
    ) -> IngressReceipt:
        if len(body) > _MAX_UPDATE_BYTES:
            raise IngressError(413, "request body is too large")

        endpoint = await self._resolve_endpoint(public_id)
        if endpoint is None:
            raise IngressError(404, "endpoint not found")
        if not endpoint["is_enabled"]:
            logger.warning(
                "ingress_endpoint_disabled",
                endpoint_id=str(endpoint["endpoint_id"]),
                reason=endpoint["disabled_reason"],
            )
            raise IngressError(404, "endpoint not found")

        client_id = endpoint["client_id"]
        endpoint_id = endpoint["endpoint_id"]
        trace_id = uuid4()
        async with tx(self._pool, str(client_id)) as conn:
            route = await db.q.fetch_one(
                "ingress/get_channel_credentials.sql",
                (endpoint_id, client_id),
                conn=conn,
            )
            if route is None or not route["is_enabled"]:
                raise IngressError(404, "endpoint not found")
            if route["channel"] != "telegram":
                raise IngressError(404, "endpoint not found")
            meta = route["meta"]
            if not isinstance(meta, Mapping):
                raise IngressError(503, "endpoint credentials are not configured")
            secret_ciphertext = meta.get(TELEGRAM_WEBHOOK_SECRET_META_KEY)
            if not isinstance(secret_ciphertext, str) or not secret_ciphertext:
                raise IngressError(503, "endpoint credentials are not configured")
            credential_id = route["credential_id"]
            try:
                expected_secret = crypto.decrypt(
                    secret_ciphertext,
                    crypto.credential_aad(
                        "channel_credentials",
                        TELEGRAM_WEBHOOK_SECRET_AAD_COLUMN,
                        str(credential_id),
                    ),
                )
            except crypto.CryptoError as exc:
                logger.error(
                    "ingress_credential_decryption_failed",
                    credential_id=str(credential_id),
                    credential="telegram_webhook_secret",
                )
                raise IngressError(503, "endpoint credentials are unavailable") from exc
            if not self._adapter.verify(supplied_secret, expected_secret):
                raise IngressError(401, "invalid webhook secret")
            if not self._rate_limiter.allow(
                endpoint_id,
                limit_per_minute=int(route["rate_limit_rpm"]),
            ):
                raise IngressError(429, "endpoint rate limit exceeded")

            runtime = (route["runtime"] or "langgraph").strip().lower()
            deployment_mode = (route["deployment_mode"] or "auto").strip().lower()
            n8n_url = route["n8n_webhook_url"]

            if runtime == "n8n":
                if not n8n_url:
                    raise IngressError(503, "n8n webhook URL not configured")
                await self._proxy_to_n8n(n8n_url, supplied_secret, body)
                if deployment_mode != "shadow":
                    logger.info(
                        "ingress_proxied_to_n8n",
                        client_id=str(client_id),
                        endpoint_id=str(endpoint_id),
                        trace_id=str(trace_id),
                    )
                    return IngressReceipt(
                        accepted=True,
                        duplicate=False,
                        trace_id=trace_id,
                        proxied=True,
                    )
                logger.info(
                    "ingress_shadow_mode_enqueued",
                    client_id=str(client_id),
                    endpoint_id=str(endpoint_id),
                    trace_id=str(trace_id),
                )

            try:
                event = self._adapter.parse(body, public_id=public_id)
            except TelegramUpdateError as exc:
                raise IngressError(422, str(exc)) from exc

            if event.staff_action_id is not None:
                raise _StaffTap(
                    client_id=client_id,
                    endpoint_id=endpoint_id,
                    trace_id=trace_id,
                    event=event,
                    staff_action_id=event.staff_action_id,
                    bot_token=self._bot_token(route, credential_id),
                )

            reply_id = event.reply_to_message_id if event.is_group else None
            if (
                event.kind == "message"
                and event.callback_query_id is None
                and (event.is_group or event.chat_id == event.sender_id)
                and await db.q.fetch_one(
                    "staff/get_awaiting_edit.sql",
                    (client_id, reply_id, reply_id, event.chat_id),
                    conn=conn,
                )
                is not None
            ):
                raise _StaffEdit(
                    client_id=client_id,
                    endpoint_id=endpoint_id,
                    trace_id=trace_id,
                    event=event,
                    payload_hash=_canonical_payload_hash(body),
                    bot_token=self._bot_token(route, credential_id),
                )
            if event.is_group:
                raise IngressError(422, "only private Telegram chats are enabled")

            contact_id, first_contact, stored_display_name = await self._resolve_contact(
                conn,
                client_id=client_id,
                event=event,
            )
            conversation = await self._resolve_conversation(
                conn,
                client_id=client_id,
                contact_id=contact_id,
                channel_thread=event.channel_thread,
            )
            envelope = Envelope(
                trace_id=trace_id,
                received_at=event.received_at,
                channel=Channel.TELEGRAM,
                idempotency_key=event.idempotency_key,
                client_id=client_id,
                contact=Contact(
                    id=contact_id,
                    external_id=event.sender_id,
                    channel=Channel.TELEGRAM,
                    display_name=stored_display_name,
                ),
                conversation=ConversationRef(
                    id=conversation["id"],
                    channel_thread=event.channel_thread,
                    turn_count=conversation["turn_count"],
                    status=conversation["status"],
                ),
                message=event.message,
                callback=event.callback,
                meta=Meta(is_first_contact=first_contact),
            )
            payload_hash = _canonical_payload_hash(body)
            raw = {
                "envelope": envelope.model_dump(mode="json"),
                "source": {
                    "channel": "telegram",
                    "update_id": event.update_id,
                    "kind": event.kind,
                },
            }
            inserted = await db.q.fetch_one(
                "ingress/insert_event.sql",
                (
                    client_id,
                    endpoint_id,
                    contact_id,
                    event.idempotency_key,
                    payload_hash,
                    trace_id,
                    Jsonb(raw),
                ),
                conn=conn,
            )
            duplicate = inserted is None
            if duplicate:
                previous = await db.q.fetch_one(
                    "ingress/get_event_hash.sql",
                    (client_id, event.idempotency_key),
                    conn=conn,
                )
                if previous is None:
                    raise RuntimeError("conflicting ingress event disappeared during lookup")
                trace_id = previous["trace_id"]
                if not hmac.compare_digest(previous["payload_hash"], payload_hash):
                    logger.error(
                        "ingress_idempotency_payload_mismatch",
                        endpoint_id=str(endpoint_id),
                        client_id=str(client_id),
                        idempotency_key=event.idempotency_key,
                    )

            answer_token = None
            if event.callback_query_id:
                encrypted_token = route["access_token"]
                if not isinstance(encrypted_token, str) or not encrypted_token:
                    logger.error(
                        "telegram_callback_answer_unavailable",
                        endpoint_id=str(endpoint_id),
                        reason="bot token missing",
                    )
                else:
                    try:
                        answer_token = crypto.decrypt(
                            encrypted_token,
                            crypto.credential_aad(
                                "channel_credentials", "access_token", str(credential_id)
                            ),
                        )
                    except crypto.CryptoError:
                        logger.error(
                            "telegram_callback_answer_unavailable",
                            endpoint_id=str(endpoint_id),
                            reason="bot token decryption failed",
                        )

        if event.callback_query_id and answer_token:
            await self._answer_callback(answer_token, event.callback_query_id)
            await self._clear_buttons(answer_token, event)

        return IngressReceipt(
            accepted=True,
            duplicate=duplicate,
            trace_id=trace_id,
            proxied=(runtime == "n8n"),
        )

    async def _proxy_to_n8n(
        self,
        url: str,
        supplied_secret: str | None,
        body: bytes,
    ) -> None:
        headers = {"Content-Type": "application/json"}
        if supplied_secret:
            headers["X-Telegram-Bot-Api-Secret-Token"] = supplied_secret
        client = self._http_client
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=15.0)
            close_client = True
        try:
            resp = await client.post(url, content=body, headers=headers)
            if resp.status_code >= 400:
                logger.error(
                    "n8n_proxy_response_error",
                    status_code=resp.status_code,
                    body=resp.text[:200],
                )
                raise IngressError(502, f"n8n proxy error ({resp.status_code})")
        except httpx.RequestError as exc:
            logger.error("n8n_proxy_connection_error", error=str(exc))
            raise IngressError(502, "n8n proxy connection failed") from exc
        finally:
            if close_client:
                await client.aclose()

    async def _resolve_endpoint(self, public_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            return await db.q.fetch_one(
                "ingress/resolve_endpoint.sql",
                (public_id,),
                conn=conn,
            )

    async def _resolve_contact(
        self,
        conn: Any,
        *,
        client_id: UUID,
        event: TelegramEvent,
    ) -> tuple[UUID, bool, str | None]:
        resolved = await db.q.fetch_one(
            "ingress/resolve_telegram_contact.sql",
            {
                "client_id": client_id,
                "sender_id": event.sender_id,
                "telegram_id": int(event.sender_id),
                "display_name": event.display_name,
            },
            conn=conn,
        )
        if resolved is not None:
            return resolved["contact_id"], resolved["first_contact"], resolved["full_name"]

        # A concurrent insert can be invisible to the statement's snapshot.
        # Resolve conflicts in subsequent statements, including identity mismatches.
        identity = await db.q.fetch_one(
            "ingress/get_contact_identity.sql",
            (client_id, event.sender_id),
            conn=conn,
        )
        created = False
        stored_name: str | None = None
        if identity is not None:
            contact_id = identity["contact_id"]
        else:
            inserted = await db.q.fetch_one(
                "ingress/insert_contact.sql",
                (client_id, int(event.sender_id), event.display_name),
                conn=conn,
            )
            if inserted is None:
                existing = await db.q.fetch_one(
                    "ingress/get_telegram_contact.sql",
                    (client_id, int(event.sender_id)),
                    conn=conn,
                )
                if existing is None:
                    raise RuntimeError("telegram contact conflict could not be resolved")
                contact_id = existing["id"]
                stored_name = existing["full_name"]
            else:
                contact_id = inserted["id"]
                stored_name = event.display_name
                created = True

            identity_inserted = await db.q.fetch_one(
                "ingress/insert_contact_identity.sql",
                (client_id, event.sender_id, contact_id),
                conn=conn,
            )
            if identity_inserted is None:
                existing_identity = await db.q.fetch_one(
                    "ingress/get_contact_identity.sql",
                    (client_id, event.sender_id),
                    conn=conn,
                )
                if existing_identity is None or existing_identity["contact_id"] != contact_id:
                    raise IngressError(409, "contact identity conflicts with an existing account")
        if identity is not None:
            existing = await db.q.fetch_one(
                "ingress/get_telegram_contact.sql",
                (client_id, int(event.sender_id)),
                conn=conn,
            )
            if existing is None or existing["id"] != contact_id:
                raise IngressError(409, "contact identity conflicts with an existing account")
            stored_name = existing["full_name"]

        return contact_id, created, stored_name

    async def _resolve_conversation(
        self,
        conn: Any,
        *,
        client_id: UUID,
        contact_id: UUID,
        channel_thread: str,
    ) -> dict[str, Any]:
        conversation = await db.q.fetch_one(
            "ingress/insert_conversation.sql",
            (client_id, contact_id, channel_thread),
            conn=conn,
        )
        if conversation is None:
            conversation = await db.q.fetch_one(
                "ingress/get_conversation.sql",
                (client_id, channel_thread),
                conn=conn,
            )
        if conversation is None:
            raise RuntimeError("telegram conversation conflict could not be resolved")
        if conversation["contact_id"] != contact_id:
            raise IngressError(
                409,
                "Telegram group chats are not supported for customer conversations",
            )
        return conversation

    @staticmethod
    def _bot_token(route: Mapping[str, Any], credential_id: UUID) -> str:
        encrypted_token = route["access_token"]
        if not isinstance(encrypted_token, str) or not encrypted_token:
            raise IngressError(503, "endpoint credentials are not configured")
        try:
            return crypto.decrypt(
                encrypted_token,
                crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
            )
        except crypto.CryptoError as exc:
            raise IngressError(503, "endpoint credentials are unavailable") from exc

    async def _clear_buttons(self, bot_token: str, event: TelegramEvent) -> None:
        """Buttons are one-shot: remove them once tapped so they cannot be pressed twice."""
        if event.source_message_id is None:
            return
        url = f"https://api.telegram.org/bot{bot_token}/editMessageReplyMarkup"
        payload = {
            "chat_id": event.chat_id,
            "message_id": event.source_message_id,
            "reply_markup": {"inline_keyboard": []},
        }
        try:
            if self._http_client is None:
                async with httpx.AsyncClient(timeout=3) as client:
                    response = await client.post(url, json=payload)
            else:
                response = await self._http_client.post(url, json=payload, timeout=3)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("telegram_clear_buttons_failed", error=type(exc).__name__)

    async def _answer_callback(
        self, bot_token: str, callback_query_id: str, text: str | None = None
    ) -> None:
        url = f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery"
        answer: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            answer["text"] = text
            answer["show_alert"] = True
        try:
            if self._http_client is None:
                async with httpx.AsyncClient(timeout=3) as client:
                    response = await client.post(url, json=answer)
            else:
                response = await self._http_client.post(
                    url,
                    json=answer,
                    timeout=3,
                )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error(
                "telegram_callback_answer_failed",
                error=type(exc).__name__,
            )


def _canonical_payload_hash(body: bytes) -> str:
    """Hash canonical JSON so whitespace or key-order differences are harmless."""
    try:
        parsed = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelegramUpdateError("request body is not valid JSON") from exc
    canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
