"""The TikTok Shop webhook (spec 6.4): verify, resolve the shop, queue a job. Nothing else.

``POST /api/v1/channels/tiktok_shop/webhook`` is public and unauthenticated, so
``TikTokWebhookHandler.receive`` works in a fixed order:

1. size limit (the route already streams with the same cap);
2. the HMAC-SHA256 signature with ``TIKTOK_APP_SECRET`` (two candidate recipes, see
   ``SIGNATURE_RECIPES``), before any parsing;
3. parse: the body must be a JSON object, else 422 and nothing is queued;
4. classify the event through ``EVENT_KINDS`` and check the ids are plain identifiers;
5. resolve the shop with ``resolve_tiktok_shop_route`` (active stores only, no tenant context);
6. queue ``order_refresh`` / ``product_refresh`` for that tenant, or disconnect the store.

An unknown event, an unknown shop or an id that is not a plain identifier is acknowledged and
dropped (200), so TikTok stops retrying; the catch-up job covers anything missed. A duplicate
delivery folds into the pending job through the pending-unique index (``enqueue_job`` returns
False). The raw payload is never stored (no ``ingress_events`` row) and never logged: logs
carry only constant event names and constant reasons, never the shop id, an order or product
id, or the signature. No TikTok call is made here, so the acknowledgement stays fast.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID

import psycopg
import structlog
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, ValidationError

from csa.agents.ecommerce.tiktok_models import PRODUCT_ID_PATTERN
from csa.core.db import q
from csa.core.tenancy import tx
from csa.marketplace.disconnect import DisconnectReason, StoreNotFoundError
from csa.marketplace.disconnect import disconnect_store as _disconnect_store
from csa.marketplace.jobs import JobKind, MarketplaceJobError
from csa.marketplace.jobs import enqueue_job as _enqueue_job

logger = structlog.get_logger(__name__)

MAX_WEBHOOK_BYTES = 1_000_000

EventKind = Literal["order", "product", "deauthorized"]

# TikTok Shop webhook types, by number and by name. The three numbers were read in Partner Center
# on 2026-11-10 (ledger row 5); the names and the payload field names are still unverified.
# Anything not listed is acknowledged and dropped.
EVENT_KINDS: dict[int | str, EventKind] = {
    1: "order",  # Order Status Change (confirmed)
    "ORDER_STATUS_CHANGE": "order",
    5: "product",  # Product Status Change, audit results (confirmed)
    "PRODUCT_STATUS_CHANGE": "product",
    6: "deauthorized",  # Seller Deauthorization (confirmed; 12 is Order Return Status Change)
    "SELLER_DEAUTHORIZATION": "deauthorized",
}

_MAX_TYPE_DIGITS = 6
# The same plain-identifier rule as product ids in the signed read path (dispatch.py).
_SAFE_ID = re.compile(PRODUCT_ID_PATTERN)

DropReason = Literal[
    "invalid_event", "unknown_event", "invalid_shop", "invalid_target", "unknown_shop"
]


@dataclass(frozen=True)
class WebhookReceipt:
    accepted: bool
    queued: bool


class WebhookRejected(Exception):
    """A refusal the route answers with ``status_code`` and the fixed safe ``code``."""

    def __init__(self, status_code: int, code: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code


class EnqueueJob(Protocol):
    """``csa.marketplace.jobs.enqueue_job``, called on the tenant's transaction."""

    async def __call__(
        self,
        conn: psycopg.AsyncConnection[Any],
        *,
        client_id: UUID,
        store_id: UUID,
        kind: JobKind,
        target: str | None = None,
    ) -> bool: ...


class DisconnectStore(Protocol):
    """``csa.marketplace.disconnect.disconnect_store``."""

    async def __call__(
        self,
        pool: AsyncConnectionPool,
        client_id: UUID,
        store_id: UUID,
        *,
        reason: DisconnectReason,
    ) -> None: ...


# The signing recipe is UNVERIFIED: verify in Partner Center (ledger Task 21): keep only the one
# that is real. Until then a signature matching EITHER recipe is accepted. Both are keyed with
# the app secret, so accepting two does not weaken the secret requirement: a forger still needs
# TIKTOK_APP_SECRET. The app key adds no secrecy (recipe B only mixes it in); it is never logged.


def signature_body_only(raw_body: bytes, app_secret: str, app_key: str) -> str:
    """Recipe A: hex HMAC-SHA256(key=app_secret, msg=raw_body).

    Verify in Partner Center (ledger Task 21): keep only the one that is real.
    """
    del app_key  # recipe A does not sign the app key
    return hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def signature_app_key_and_body(raw_body: bytes, app_secret: str, app_key: str) -> str:
    """Recipe B: hex HMAC-SHA256(key=app_secret, msg=app_key (UTF-8) + raw_body).

    Verify in Partner Center (ledger Task 21): keep only the one that is real.
    """
    message = app_key.encode("utf-8") + raw_body
    return hmac.new(app_secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


SIGNATURE_RECIPES = (signature_body_only, signature_app_key_and_body)


def verify_tiktok_signature(raw_body: bytes, signature: str, app_secret: str, app_key: str) -> bool:
    """Constant-time check of the signature against every recipe in ``SIGNATURE_RECIPES``.

    A ``sha256=`` or ``Bearer `` prefix is accepted. Compared as bytes, so a header with
    non-ASCII characters is simply a mismatch. Every recipe is compared (no early exit).
    """
    sig = signature.strip()
    if sig.startswith("sha256="):
        sig = sig[7:]
    elif sig.startswith("Bearer "):
        sig = sig[7:]
    candidate = sig.lower().encode("utf-8", errors="replace")
    matches = [
        hmac.compare_digest(recipe(raw_body, app_secret, app_key).encode("ascii"), candidate)
        for recipe in SIGNATURE_RECIPES
    ]
    return any(matches)


def event_kind(value: object) -> EventKind | None:
    """The kind for a webhook ``type``, given as a number, a numeric string or a name."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return EVENT_KINDS.get(value)
    if isinstance(value, str):
        text = value.strip()
        if text.isdecimal():
            return EVENT_KINDS.get(int(text)) if len(text) <= _MAX_TYPE_DIGITS else None
        return EVENT_KINDS.get(text.upper())
    return None


class _Event(BaseModel):
    """The fields the webhook uses; everything else in the payload is ignored."""

    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)

    type: object = None
    shop_id: StrictStr | StrictInt | None = None
    data: dict[str, Any] | None = None


@dataclass(frozen=True)
class _Classified:
    kind: EventKind
    shop_id: str
    target: str | None


def _plain_id(value: object) -> str | None:
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    text = str(value)
    return text if _SAFE_ID.fullmatch(text) else None


def _classify(payload: dict[str, Any]) -> _Classified | DropReason:
    try:
        parsed = _Event.model_validate(payload)
    except ValidationError:
        return "invalid_event"
    kind = event_kind(parsed.type)
    if kind is None:
        return "unknown_event"
    shop_id = _plain_id(parsed.shop_id)
    if shop_id is None:
        return "invalid_shop"
    if kind == "deauthorized":
        return _Classified(kind=kind, shop_id=shop_id, target=None)
    field = "order_id" if kind == "order" else "product_id"
    target = _plain_id((parsed.data or {}).get(field))
    if target is None:
        return "invalid_target"
    return _Classified(kind=kind, shop_id=shop_id, target=target)


def _parse(raw_body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw_body)
    except (ValueError, RecursionError):  # includes invalid UTF-8 and over-long integers
        raise WebhookRejected(422, "invalid_payload") from None
    if not isinstance(payload, dict):
        raise WebhookRejected(422, "invalid_payload")
    return payload


class TikTokWebhookHandler:
    """Verifies one webhook delivery and turns it into at most one job or one disconnect.

    Built per request by the route from the app pool and settings; ``app_secret`` or
    ``app_key`` None or empty means TikTok is off (404). ``enqueue_job`` and
    ``disconnect_store`` default to the real marketplace functions.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        app_secret: str | None,
        app_key: str | None,
        *,
        enqueue_job: EnqueueJob = _enqueue_job,
        disconnect_store: DisconnectStore = _disconnect_store,
    ) -> None:
        self._pool = pool
        self._app_secret = app_secret
        self._app_key = app_key
        self._enqueue_job = enqueue_job
        self._disconnect_store = disconnect_store

    async def receive(self, *, raw_body: bytes, signature: str | None) -> WebhookReceipt:
        """Verify, resolve and queue (see the module docstring); raises ``WebhookRejected``."""
        if not self._app_secret or not self._app_key:
            raise WebhookRejected(404, "not_found")
        if len(raw_body) > MAX_WEBHOOK_BYTES:
            raise WebhookRejected(413, "body_too_large")
        if not signature:
            logger.warning("tiktok_webhook_rejected", reason="signature_required")
            raise WebhookRejected(401, "signature_required")
        if not verify_tiktok_signature(raw_body, signature, self._app_secret, self._app_key):
            logger.warning("tiktok_webhook_rejected", reason="invalid_signature")
            raise WebhookRejected(401, "invalid_signature")
        payload = _parse(raw_body)

        event = _classify(payload)
        if isinstance(event, str):
            return _dropped(event)
        route = await self._resolve(event.shop_id)
        if route is None:
            return _dropped("unknown_shop")
        client_id, store_id = route

        if event.kind == "deauthorized":
            try:
                await self._disconnect_store(self._pool, client_id, store_id, reason="deauthorized")
            except StoreNotFoundError:  # disconnected or removed since it resolved
                return _dropped("unknown_shop")
            logger.info("tiktok_webhook_store_deauthorized")
            return WebhookReceipt(accepted=True, queued=False)

        job: JobKind = "order_refresh" if event.kind == "order" else "product_refresh"
        try:
            async with tx(self._pool, str(client_id)) as conn:
                queued = await self._enqueue_job(
                    conn, client_id=client_id, store_id=store_id, kind=job, target=event.target
                )
        except MarketplaceJobError:  # the store was removed since it resolved
            return _dropped("unknown_shop")
        logger.info("tiktok_webhook_queued" if queued else "tiktok_webhook_folded", kind=job)
        return WebhookReceipt(accepted=True, queued=queued)

    async def _resolve(self, shop_id: str) -> tuple[UUID, UUID] | None:
        async with self._pool.connection() as conn:
            row = await q.fetch_one(
                "marketplace/resolve_tiktok_shop_route.sql", {"shop_id": shop_id}, conn=conn
            )
        return (row["client_id"], row["store_id"]) if row is not None else None


def _dropped(reason: DropReason) -> WebhookReceipt:
    logger.info("tiktok_webhook_dropped", reason=reason)
    return WebhookReceipt(accepted=True, queued=False)
