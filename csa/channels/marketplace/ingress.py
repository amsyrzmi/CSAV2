"""TikTok Shop webhook ingress: verification, deduplication, persistence (LG13/09)."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.channels.ingress import EndpointRateLimiter, IngressError
from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

_MAX_WEBHOOK_BYTES = 1_000_000


@dataclass(frozen=True)
class MarketplaceWebhookReceipt:
    accepted: bool
    duplicate: bool
    trace_id: UUID
    shop_id: str | None = None


def verify_tiktok_signature(raw_body: bytes, signature: str, app_secret: str) -> bool:
    """Verify HMAC-SHA256 signature for TikTok Shop webhook payload."""
    sig = signature.strip()
    if sig.startswith("sha256="):
        sig = sig[7:]
    elif sig.startswith("Bearer "):
        sig = sig[7:]
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.lower(), sig.lower())


class TikTokWebhookIngress:
    """Verifies and persists TikTok Shop webhook events into ingress_events."""

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        app_secret: str | None = None,
        rate_limiter: EndpointRateLimiter | None = None,
    ) -> None:
        self._pool = pool
        self._app_secret = app_secret
        self._rate_limiter = rate_limiter or EndpointRateLimiter()

    async def receive(
        self,
        *,
        raw_body: bytes,
        signature: str | None,
        public_id: str | None = None,
    ) -> MarketplaceWebhookReceipt:
        """Verify, resolve endpoint, deduplicate, and record the inbound webhook."""
        if len(raw_body) > _MAX_WEBHOOK_BYTES:
            raise IngressError(413, "Request body is too large")

        # 1. Signature verification
        if not signature:
            logger.warning("tiktok_webhook_missing_signature")
            raise IngressError(401, "Missing webhook signature")

        secret = self._app_secret
        if not secret:
            logger.error("tiktok_app_secret_unconfigured")
            raise IngressError(503, "TikTok Shop App secret not configured")

        if not verify_tiktok_signature(raw_body, signature, secret):
            logger.warning("tiktok_webhook_invalid_signature")
            raise IngressError(401, "Invalid webhook signature")

        # 2. Parse payload
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except Exception as exc:
            raise IngressError(422, "Malformed JSON webhook payload") from exc

        if not isinstance(payload, dict):
            raise IngressError(422, "Webhook payload must be a JSON object")

        shop_id = str(payload.get("shop_id") or payload.get("shop_cipher") or "")
        event_type = payload.get("type", "unknown")
        data_block = payload.get("data")
        order_id = data_block.get("order_id") if isinstance(data_block, dict) else ""
        event_id = str(payload.get("event_id") or payload.get("tts_event_id") or order_id or "")

        # 3. Resolve endpoint
        candidate_id = public_id or shop_id
        if not candidate_id:
            raise IngressError(422, "Cannot determine shop_id or endpoint_id from webhook")

        endpoint = await self._resolve_endpoint(candidate_id)
        if endpoint is None and not public_id and shop_id:
            # Fall back to try prefixed endpoint 'tt_<shop_id>'
            endpoint = await self._resolve_endpoint(f"tt_{shop_id}")

        if endpoint is None:
            logger.warning("tiktok_webhook_endpoint_not_found", candidate_id=candidate_id)
            raise IngressError(404, "Endpoint not found")

        if not endpoint["is_enabled"]:
            logger.warning("tiktok_webhook_endpoint_disabled", candidate_id=candidate_id)
            raise IngressError(404, "Endpoint is disabled")

        endpoint_id = endpoint["endpoint_id"]
        client_id = endpoint["client_id"]
        rate_limit = int(endpoint["rate_limit_rpm"] or 120)

        # 4. Rate limiting
        if not self._rate_limiter.allow(endpoint_id, limit_per_minute=rate_limit):
            logger.warning("tiktok_webhook_rate_limited", endpoint_id=str(endpoint_id))
            raise IngressError(429, "Endpoint rate limit exceeded")

        # 5. Deduplication and persistence
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        idempotency_key = f"tiktok_shop:{shop_id}:{event_type}:{event_id or payload_hash[:16]}"
        trace_id = uuid4()

        raw_event: dict[str, Any] = {
            "channel": "tiktok_shop",
            "shop_id": shop_id,
            "event_type": event_type,
            "event_id": event_id,
            "payload": payload,
            "received_at": datetime.now(UTC).isoformat(),
        }

        async with tx(self._pool, str(client_id)) as conn:
            inserted = await db.q.fetch_one(
                "ingress/insert_event.sql",
                (
                    client_id,
                    endpoint_id,
                    None,  # contact_id is None for channel marketplace webhooks
                    idempotency_key,
                    payload_hash,
                    trace_id,
                    Jsonb(raw_event),
                ),
                conn=conn,
            )
            duplicate = inserted is None
            if duplicate:
                prev = await db.q.fetch_one(
                    "ingress/get_event_hash.sql",
                    (client_id, idempotency_key),
                    conn=conn,
                )
                if prev:
                    trace_id = prev["trace_id"]
                logger.info(
                    "tiktok_webhook_duplicate_acknowledged",
                    client_id=str(client_id),
                    idempotency_key=idempotency_key,
                    trace_id=str(trace_id),
                )
            else:
                logger.info(
                    "tiktok_webhook_enqueued",
                    client_id=str(client_id),
                    endpoint_id=str(endpoint_id),
                    shop_id=shop_id,
                    event_type=event_type,
                    trace_id=str(trace_id),
                )

        return MarketplaceWebhookReceipt(
            accepted=True,
            duplicate=duplicate,
            trace_id=trace_id,
            shop_id=shop_id,
        )

    async def _resolve_endpoint(self, public_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as conn:
            return await db.q.fetch_one(
                "ingress/resolve_endpoint.sql",
                (public_id,),
                conn=conn,
            )
