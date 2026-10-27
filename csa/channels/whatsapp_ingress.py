"""Signed, number-routed durable ingress for the WhatsApp Cloud API."""

from __future__ import annotations

import hmac
import json
from typing import Any
from uuid import UUID, uuid4

import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.channels.ingress import (
    _MAX_UPDATE_BYTES,
    EndpointRateLimiter,
    IngressError,
    IngressReceipt,
    _canonical_payload_hash,
)
from csa.channels.whatsapp import WhatsAppAdapter, WhatsAppEvent, WhatsAppParseError
from csa.contracts.envelope import Channel, Contact, ConversationRef, Envelope, Message, Meta
from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)


class WhatsAppIngress:
    """Verify, normalize, tenant-bind, and persist WhatsApp webhook deliveries."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        app_secret: str,
        verify_token: str,
        adapter: type[WhatsAppAdapter] = WhatsAppAdapter,
        rate_limiter: EndpointRateLimiter | None = None,
    ) -> None:
        self._pool = pool
        self._app_secret = app_secret
        self._verify_token = verify_token
        self._adapter = adapter
        self._rate_limiter = rate_limiter or EndpointRateLimiter()

    def challenge(self, mode: str | None, token: str | None, challenge: str | None) -> str | None:
        """Answer Meta's subscription handshake; None means reject."""
        return self._adapter.verify_challenge(self._verify_token, mode, token, challenge)

    async def receive(self, signature_header: str | None, body: bytes) -> list[IngressReceipt]:
        """Persist every routable event of a verified delivery."""
        if len(body) > _MAX_UPDATE_BYTES:
            raise IngressError(413, "request body is too large")
        if not self._adapter.verify_signature(self._app_secret, body, signature_header):
            raise IngressError(403, "invalid signature")
        try:
            events = self._adapter.parse(body)
        except WhatsAppParseError as exc:
            raise IngressError(422, str(exc)) from exc

        receipts: list[IngressReceipt] = []
        for event in events:
            receipt = await self._handle_event(event)
            if receipt is not None:
                receipts.append(receipt)
        return receipts

    async def _handle_event(self, event: WhatsAppEvent) -> IngressReceipt | None:
        async with self._pool.connection() as conn:
            # The row carries a ciphertext token: never log it.
            route = await db.q.fetch_one(
                "ingress/resolve_whatsapp_route.sql", (event.phone_number_id,), conn=conn
            )
        if route is None or not route["is_enabled"]:
            logger.warning("whatsapp_number_unknown", phone_number_id=event.phone_number_id)
            return None
        client_id: UUID = route["client_id"]
        endpoint_id: UUID = route["endpoint_id"]
        if (route["runtime"] or "langgraph").strip().lower() == "n8n":
            raise IngressError(503, "whatsapp_runtime_unsupported")
        if not self._rate_limiter.allow(endpoint_id, limit_per_minute=int(route["rate_limit_rpm"])):
            raise IngressError(429, "endpoint rate limit exceeded")

        if event.kind == "status":
            await self._record_status(client_id, event)
            return None

        trace_id = uuid4()
        payload_hash = _canonical_payload_hash(
            json.dumps(event.model_dump(mode="json"), sort_keys=True).encode()
        )
        async with tx(self._pool, str(client_id)) as conn:
            params = {
                "client_id": client_id,
                "wa_id": event.wa_id,
                "display_name": event.profile_name or f"+{event.wa_id}",
            }
            await db.q.fetch_one("ingress/lock_whatsapp_contact.sql", params, conn=conn)
            resolved = await db.q.fetch_one(
                "ingress/resolve_whatsapp_contact.sql", params, conn=conn
            )
            if resolved is None:
                raise RuntimeError("whatsapp contact could not be resolved")
            contact_id: UUID = resolved["contact_id"]
            conversation = await db.q.fetch_one(
                "ingress/insert_conversation.sql",
                (client_id, contact_id, "whatsapp", event.channel_thread),
                conn=conn,
            )
            if conversation is None:
                conversation = await db.q.fetch_one(
                    "ingress/get_conversation.sql",
                    (client_id, "whatsapp", event.channel_thread),
                    conn=conn,
                )
            if conversation is None:
                raise RuntimeError("whatsapp conversation conflict could not be resolved")
            if event.kind == "message":
                await db.q.execute(
                    "ingress/touch_last_inbound.sql", (client_id, conversation["id"]), conn=conn
                )
            message = event.message or Message(body_raw="", body_redacted="", language="und")
            envelope = Envelope(
                trace_id=trace_id,
                received_at=event.received_at,
                channel=Channel.WHATSAPP,
                idempotency_key=event.provider_message_id,
                client_id=client_id,
                contact=Contact(
                    id=contact_id,
                    external_id=event.wa_id,
                    channel=Channel.WHATSAPP,
                    display_name=resolved["full_name"],
                ),
                conversation=ConversationRef(
                    id=conversation["id"],
                    channel_thread=event.channel_thread,
                    turn_count=conversation["turn_count"],
                    status=conversation["status"],
                ),
                message=message,
                meta=Meta(is_first_contact=resolved["first_contact"]),
            )
            raw: dict[str, Any] = {
                "envelope": envelope.model_dump(mode="json"),
                "source": {
                    "channel": "whatsapp",
                    "kind": event.kind,
                    "phone_number_id": event.phone_number_id,
                },
            }
            inserted = await db.q.fetch_one(
                "ingress/insert_event.sql",
                (
                    client_id,
                    endpoint_id,
                    contact_id,
                    event.provider_message_id,
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
                    (client_id, event.provider_message_id),
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
                        idempotency_key=event.provider_message_id,
                    )
        if event.kind == "unsupported" and not duplicate:
            logger.info(
                "whatsapp_unsupported_type",
                client_id=str(client_id),
                unsupported_type=event.unsupported_type,
            )
        return IngressReceipt(accepted=True, duplicate=duplicate, trace_id=trace_id)

    async def _record_status(self, client_id: UUID, event: WhatsAppEvent) -> None:
        if event.status is None:
            return
        async with tx(self._pool, str(client_id)) as conn:
            await db.q.fetch_one(
                "delivery/record_provider_status.sql",
                {
                    "client_id": client_id,
                    "provider_message_id": event.status.provider_message_id,
                    "status": event.status.status,
                },
                conn=conn,
            )
