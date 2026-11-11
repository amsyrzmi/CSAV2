"""Connect, reconnect and disconnect a TikTok Shop store behind an Admin console session.

The browser never supplies a shop, app material or tokens. Connect starts a single-use attempt
and returns TikTok's authorize URL. TikTok sends the seller back to the callback, which
completes that attempt and answers with a 303 to the Connections tab carrying only a
fixed reason code (spec 6.2). Code, state, tokens and exception text are never echoed or logged.
"""

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal, NamedTuple, Protocol
from urllib.parse import urlencode
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import Field, SecretStr, ValidationError

from csa.agents.ecommerce.app_loader import AppLoader, build_app_loader
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokFirstConnectContext,
    TikTokGrantState,
)
from csa.agents.ecommerce.authorization_verifier import (
    TikTokAuthorizationVerifier,
    TikTokFirstConnectVerifier,
)
from csa.agents.ecommerce.connections import (
    AuthorizationAttempt,
    StartedAuthorization,
    VerifiedAuthorizationResult,
    VerifiedFirstConnectResult,
    begin_authorization,
    begin_first_authorization,
    complete_authorization,
    complete_first_authorization,
    create_pending_store,
)
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.stores import PendingStoreBinding, StoreBinding
from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.services import require_package
from csa.core import crypto, db
from csa.core.settings import Settings
from csa.core.tenancy import tx
from csa.marketplace.disconnect import DisconnectReason, StoreNotFoundError, disconnect_store
from csa.marketplace.jobs import enqueue_job

logger = structlog.get_logger(__name__)

# Verify the Malaysia authorize host in Partner Center (ledger Task 21).
AUTHORIZE_URL = "https://services.tiktokshop.com/open/authorize"

# Every reason the callback can put in its redirect; the Connections tab words each one.
REASONS = (
    "tiktok_unavailable",
    "provider_denied",
    "state_invalid",
    "multiple_shops",
    "no_authorized_shop",
    "shop_in_use",
    "insufficient_scope",
    "connect_failed",
)
Reason = Literal[
    "tiktok_unavailable",
    "provider_denied",
    "state_invalid",
    "multiple_shops",
    "no_authorized_shop",
    "shop_in_use",
    "insufficient_scope",
    "connect_failed",
]
# Safe codes from the attempt, verifier and completion that get their own reason. Anything not
# listed, including authorized_shop_verification_failed (a reconnect that approved a different
# shop, or a failed shop lookup), is connect_failed.
_REASON_FOR_CODE: dict[str, Reason] = {
    "multiple_shops": "multiple_shops",
    "no_authorized_shop": "no_authorized_shop",
    "shop_in_use": "shop_in_use",
    "insufficient_scope": "insufficient_scope",
    # The link is stale: expired, used, or the store changed since Connect was pressed.
    "authorization_attempt_unavailable": "state_invalid",
    "expired_authorization_attempt": "state_invalid",
    "missing_authorization_input": "state_invalid",
    "credential_changed": "state_invalid",
    # The app row was disabled or rotated meanwhile: TikTok is off on this server for now.
    "app_binding_unavailable": "tiktok_unavailable",
    "app_revision_unavailable": "tiktok_unavailable",
}
_FIRST_SYNC = ("backfill_orders", "backfill_products")
_MARKETPLACES = frozenset({"marketplaces"})
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}
# The state sent to TikTok: client id and attempt id as 32 lowercase hex each (not secret),
# then the attempt's secret, secrets.token_urlsafe(32) in connections._create_attempt.
_STATE = re.compile(
    r"(?P<client>[0-9a-f]{32})(?P<attempt>[0-9a-f]{32})(?P<secret>[A-Za-z0-9_-]{43})"
)
_MAX_CODE = 2048

FirstSyncKind = Literal["backfill_orders", "backfill_products"]
FirstVerify = Callable[
    [TikTokFirstConnectContext, SecretStr], Awaitable[VerifiedFirstConnectResult]
]
ReconnectVerify = Callable[
    [TikTokAuthorizationContext, SecretStr], Awaitable[VerifiedAuthorizationResult]
]


class EnqueueJob(Protocol):
    """``csa.marketplace.jobs.enqueue_job``, called on the tenant's transaction."""

    async def __call__(
        self,
        conn: AsyncConnection[Any],
        *,
        client_id: UUID,
        store_id: UUID,
        kind: FirstSyncKind,
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


@dataclass(frozen=True)
class TikTokConnect:
    """Server-side wiring for the three routes, kept on ``app.state.tiktok_connect``.

    ``enqueue_job`` and ``disconnect_store`` are the real marketplace functions; tests replace
    them with recording doubles through ``dataclasses.replace``.
    """

    enabled: bool
    service_id: str | None
    app_id: str
    load_app: AppLoader
    verify_first: FirstVerify
    verify_reconnect: ReconnectVerify
    encrypt: Callable[[str, bytes], str] = crypto.encrypt
    enqueue_job: EnqueueJob = enqueue_job
    disconnect_store: DisconnectStore = disconnect_store

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.service_id)


def build_tiktok_connect(settings: Settings, pool: AsyncConnectionPool) -> TikTokConnect:
    """Production wiring: settings, the registry-backed app loader and the real verifiers."""
    load_app = build_app_loader(settings, pool)
    return TikTokConnect(
        enabled=settings.tiktok_enabled,
        service_id=settings.TIKTOK_SERVICE_ID,
        app_id=settings.TIKTOK_APP_ID,
        load_app=load_app,
        verify_first=TikTokFirstConnectVerifier(load_app=load_app),
        verify_reconnect=TikTokAuthorizationVerifier(load_app=load_app),
        enqueue_job=enqueue_job,
        disconnect_store=disconnect_store,
    )


class TikTokConnectIn(Contract):
    store_id: UUID | None = None


class TikTokConnectOut(Contract):
    authorization_url: str = Field(min_length=1)


class ParsedState(NamedTuple):
    client_id: UUID
    attempt_id: UUID
    secret: SecretStr


def _now() -> datetime:
    return datetime.now(UTC)


def compose_state(client_id: UUID, started: StartedAuthorization) -> str:
    return f"{client_id.hex}{started.attempt.id.hex}{started.state.get_secret_value()}"


def parse_state(value: str | None) -> ParsedState | None:
    """Strict split of the state; anything else is refused before the database is touched."""
    match = _STATE.fullmatch(value or "")
    if match is None:
        return None
    return ParsedState(
        UUID(hex=match["client"]), UUID(hex=match["attempt"]), SecretStr(match["secret"])
    )


def _wiring(request: Request) -> TikTokConnect | None:
    wiring = getattr(request.app.state, "tiktok_connect", None)
    return wiring if isinstance(wiring, TikTokConnect) else None


def _actor(request: Request) -> ConsoleActor:
    actor = request.state.console_actor
    if not isinstance(actor, ConsoleActor):
        raise HTTPException(403, "store_admin_required")
    return actor


def require_admin_role(actor: ConsoleActor) -> None:
    """Platform admins and company Admins only; Staff and Read-only are refused."""
    if not actor.platform_admin and actor.role != Role.TENANT_ADMIN:
        raise HTTPException(403, "store_admin_required")


def require_store_admin(actor: ConsoleActor, client_id: UUID) -> None:
    require_admin_role(actor)
    if not actor.platform_admin and actor.client_id != client_id:
        raise HTTPException(403, "store_admin_required")


async def _store(pool: AsyncConnectionPool, client_id: UUID, store_id: UUID) -> dict[str, Any]:
    async with tx(pool, str(client_id)) as conn:
        row: dict[str, Any] | None = await db.q.fetch_one(
            "marketplace/get_store_for_connect.sql",
            {"client_id": client_id, "store_id": store_id},
            conn=conn,
        )
    if row is None:
        raise HTTPException(404, "store_not_found")
    return row


async def _pending_store(pool: AsyncConnectionPool, client_id: UUID) -> tuple[UUID, int]:
    """The tenant's pending store and its generation: reused if one exists, else created."""
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one(
            "marketplace/find_pending_store.sql", {"client_id": client_id}, conn=conn
        )
        if row is not None:
            return row["id"], row["credential_generation"]
        # INSERT does not fire the 0320 fence, so a new pending store is at generation 1.
        return await create_pending_store(conn, client_id), 1


async def _begin_first(
    pool: AsyncConnectionPool,
    client_id: UUID,
    app: TikTokAppMaterial,
    record_id: UUID | None = None,
    generation: int = 1,
) -> StartedAuthorization:
    if record_id is None:
        record_id, generation = await _pending_store(pool, client_id)
    context = TikTokFirstConnectContext(
        binding=PendingStoreBinding(
            client_id=client_id, record_id=record_id, platform="tiktok_shop"
        ),
        app_id=app.app_id,
        app_revision=app.revision,
        credential_generation=generation,
    )
    return await begin_first_authorization(pool, context)


async def _begin_reconnect(
    pool: AsyncConnectionPool, client_id: UUID, app: TikTokAppMaterial, store_id: UUID
) -> StartedAuthorization:
    row = await _store(pool, client_id, store_id)
    if row["shop_id"] is None:
        # Still waiting for TikTok approval: try the first connect again on the same row.
        return await _begin_first(
            pool, client_id, app, record_id=row["id"], generation=row["credential_generation"]
        )
    try:
        context = TikTokAuthorizationContext(
            binding=StoreBinding(
                client_id=client_id,
                record_id=row["id"],
                platform="tiktok_shop",
                shop_id=row["shop_id"],
            ),
            app_id=app.app_id,
            app_revision=app.revision,
            credential_generation=row["credential_generation"],
        )
    except ValidationError:
        raise HTTPException(409, "store_changed") from None
    return await begin_authorization(pool, context)


async def pending_attempt_kind(
    pool: AsyncConnectionPool, state: ParsedState
) -> Literal["first_connect", "reconnect"] | None:
    """Which completer owns the attempt, or None when it is unknown, used or expired.

    Read-only: the completers themselves consume the attempt under their fences.
    """
    hashed = hashlib.sha256(state.secret.get_secret_value().encode()).hexdigest()
    async with tx(pool, str(state.client_id)) as conn:
        row = await db.q.fetch_one(
            "ecommerce/get_authorization_attempt.sql",
            (state.client_id, state.attempt_id, hashed),
            conn=conn,
        )
    if row is None:
        return None
    attempt = AuthorizationAttempt.model_validate(row)
    if attempt.status != "pending" or attempt.expires_at <= _now():
        return None
    return (
        "first_connect" if isinstance(attempt.context, TikTokFirstConnectContext) else "reconnect"
    )


async def _queue_first_sync(
    pool: AsyncConnectionPool, wiring: TikTokConnect, grant: TikTokGrantState
) -> None:
    """Queue the first sync; the store is connected either way, so a failure is only logged."""
    binding = grant.context.binding
    try:
        async with tx(pool, str(binding.client_id)) as conn:
            for kind in _FIRST_SYNC:
                await wiring.enqueue_job(
                    conn, client_id=binding.client_id, store_id=binding.record_id, kind=kind
                )
    except Exception as exc:  # logged by type only; the connect itself stands
        logger.warning(
            "tiktok_first_sync_not_queued",
            client_id=str(binding.client_id),
            store_id=str(binding.record_id),
            error=type(exc).__name__,
        )


async def _complete(
    request: Request,
    actor: ConsoleActor,
    state: ParsedState | None,
    code: str | None,
    error: str | None,
    duplicated: bool,
) -> Reason | None:
    """Return None on success, else the reason; only complete_* changes anything."""
    wiring = _wiring(request)
    if wiring is None or not wiring.available:
        return "tiktok_unavailable"
    if error is not None:
        return "provider_denied"
    if duplicated or state is None or not code or len(code) > _MAX_CODE:
        return "state_invalid"
    if not actor.platform_admin and actor.client_id != state.client_id:
        return "state_invalid"
    pool: AsyncConnectionPool = request.app.state.pool
    client_id = state.client_id
    try:
        await require_package(pool, client_id, _MARKETPLACES)
        kind = await pending_attempt_kind(pool, state)
        if kind is None:
            return "state_invalid"
        authorization_code = SecretStr(code)
        if kind == "first_connect":
            grant = await complete_first_authorization(
                pool,
                client_id,
                state.attempt_id,
                state=state.secret,
                authorization_code=authorization_code,
                verify=wiring.verify_first,
                encrypt=wiring.encrypt,
            )
        else:
            grant = await complete_authorization(
                pool,
                client_id,
                state.attempt_id,
                state=state.secret,
                authorization_code=authorization_code,
                verify=wiring.verify_reconnect,
                encrypt=wiring.encrypt,
            )
    except TikTokAuthorizationError as exc:
        reason = _REASON_FOR_CODE.get(exc.code, "connect_failed")
        logger.warning(
            "tiktok_connect_failed", client_id=str(client_id), reason=reason, code=exc.code
        )
        return reason
    except HTTPException as exc:
        # Only require_package raises one here: the state's client lacks the marketplaces
        # package (the callback's stand-in for package_gate). Its detail is our own fixed code.
        logger.warning(
            "tiktok_connect_failed",
            client_id=str(client_id),
            reason="connect_failed",
            code=exc.detail if exc.detail == "package_inactive" else "http_refused",
        )
        return "connect_failed"
    except Exception as exc:  # never a 500 page or exception text; logged by type only
        logger.error(
            "tiktok_connect_failed",
            client_id=str(client_id),
            reason="connect_failed",
            error=type(exc).__name__,
        )
        return "connect_failed"
    await _queue_first_sync(pool, wiring, grant)
    logger.info("tiktok_connected", client_id=str(client_id), kind=kind)
    return None


def build_tiktok_connect_router() -> APIRouter:
    """Connect and disconnect; included in the Business router behind its package gate."""
    router = APIRouter()

    @router.post("/stores/tiktok/connect", response_model=TikTokConnectOut)
    async def connect(
        request: Request,
        response: Response,
        body: TikTokConnectIn,
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> TikTokConnectOut:
        require_store_admin(_actor(request), client_id)
        pool: AsyncConnectionPool = request.app.state.pool
        await require_package(pool, client_id, _MARKETPLACES)
        wiring = _wiring(request)
        app = await wiring.load_app(wiring.app_id) if wiring and wiring.available else None
        if wiring is None or wiring.service_id is None or app is None:
            raise HTTPException(503, "tiktok_unavailable")
        try:
            if body.store_id is None:
                started = await _begin_first(pool, client_id, app)
            else:
                started = await _begin_reconnect(pool, client_id, app, body.store_id)
        except TikTokAuthorizationError as exc:
            if _REASON_FOR_CODE.get(exc.code) == "tiktok_unavailable":
                raise HTTPException(503, "tiktok_unavailable") from None
            raise HTTPException(409, "store_changed") from None
        query = urlencode(
            {"service_id": wiring.service_id, "state": compose_state(client_id, started)}
        )
        response.headers.update(_NO_STORE)
        return TikTokConnectOut(authorization_url=f"{AUTHORIZE_URL}?{query}")

    @router.post("/stores/{store_id}/disconnect", status_code=204)
    async def disconnect(
        request: Request, store_id: UUID, client_id: Annotated[UUID, Depends(tenant)]
    ) -> Response:
        require_store_admin(_actor(request), client_id)
        pool: AsyncConnectionPool = request.app.state.pool
        await require_package(pool, client_id, _MARKETPLACES)
        # Disconnecting is local, so it works while TikTok is off or not wired at all.
        wiring = _wiring(request)
        disconnect = wiring.disconnect_store if wiring is not None else disconnect_store
        row = await _store(pool, client_id, store_id)
        try:
            await disconnect(pool, client_id, row["id"], reason="admin")
        except StoreNotFoundError:
            # A pending store (no shop yet) has nothing to disconnect; the row is not a store
            # the admin can act on, so it answers like a missing one.
            raise HTTPException(404, "store_not_found") from None
        return Response(status_code=204, headers=_NO_STORE)

    return router


def build_tiktok_callback_router() -> APIRouter:
    """The callback; its tenant comes from the state, so it cannot use the tenant gate.

    The Business router includes it behind the console session and ``business_actor``; it
    checks the Admin role, the state's client and the marketplaces package itself.
    """
    router = APIRouter()

    @router.get(
        "/stores/tiktok/callback",
        status_code=303,
        response_class=RedirectResponse,
        responses={303: {"description": "Back to Connections with a safe outcome."}},
    )
    async def callback(
        request: Request,
        code: Annotated[str | None, Query()] = None,
        state: Annotated[str | None, Query()] = None,
        error: Annotated[str | None, Query()] = None,
    ) -> Response:
        actor = _actor(request)
        require_admin_role(actor)
        query = request.query_params
        duplicated = any(len(query.getlist(key)) > 1 for key in ("code", "state", "error"))
        parsed = parse_state(state)
        reason = await _complete(request, actor, parsed, code, error, duplicated)
        params: dict[str, str] = {}
        if actor.platform_admin:
            target = "/connections"
            if parsed is not None:
                params["client_id"] = str(parsed.client_id)
        else:
            target = "/portal/connections"
        if reason is None:
            params["tiktok"] = "connected"
        else:
            params.update({"tiktok": "failed", "reason": reason})
        response = RedirectResponse(f"{target}?{urlencode(params)}", status_code=303)
        response.headers.update(_NO_STORE)
        return response

    return router
