"""Offline authorization choreography; no provider exchange, callback route or key source."""

import asyncio
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    SecretStr,
    Tag,
    ValidationError,
)

from csa.agents.ecommerce.apps import require_app
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokFirstConnectContext,
    TikTokGrantState,
    TikTokReadOperation,
    require_tiktok_grant,
    require_tiktok_product_scope,
    require_tiktok_scopes,
)
from csa.agents.ecommerce.grants import insert_verified_grant
from csa.agents.ecommerce.storage import insert_verified_shop_binding
from csa.agents.ecommerce.stores import StoreBinding
from csa.core import db
from csa.core.crypto import CryptoError, credential_aad
from csa.core.tenancy import tx

EXCHANGE_TIMEOUT_SECONDS = 15
# The unique keys that hold a TikTok shop: one active store per shop across the deployment (0491)
# and one row per shop within a tenant (baseline). Hitting either means the shop is in use.
_SHOP_KEYS = frozenset(
    {"channel_credentials_tiktok_shop_key", "channel_credentials_client_id_channel_shop_id_key"}
)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


def _attempt_kind(value: Any) -> str | None:
    """Tell the stored context apart by whether its binding names a shop, nothing else.

    A binding with a shop_id key (even null or empty) is a reconnect context and validates as
    strictly as before; only a binding without the key can be a first connect.
    """
    if isinstance(value, TikTokAuthorizationContext):
        return "reconnect"
    if isinstance(value, TikTokFirstConnectContext):
        return "first_connect"
    if isinstance(value, dict):
        binding = value.get("binding")
        if isinstance(binding, dict):
            return "reconnect" if "shop_id" in binding else "first_connect"
        if binding is not None and hasattr(binding, "shop_id"):
            return "reconnect"
        return "first_connect"
    return None


AttemptContext = Annotated[
    Annotated[TikTokAuthorizationContext, Tag("reconnect")]
    | Annotated[TikTokFirstConnectContext, Tag("first_connect")],
    Discriminator(_attempt_kind),
]


class AuthorizationAttempt(_Contract):
    id: UUID
    context: AttemptContext
    status: Literal["pending", "cancelled", "consumed", "completed"]
    created_at: AwareDatetime
    expires_at: AwareDatetime


class StartedAuthorization(_Contract):
    attempt: AuthorizationAttempt
    state: SecretStr


class VerifiedAuthorizationResult(_Contract):
    """Trusted verifier output, not a provider response schema or browser input.

    Verifier must authenticate app identity/revision, exact intended shop and scopes.
    Merely copying the requested context into arbitrary token data is not verification.
    """

    context: TikTokAuthorizationContext
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = Field(default=None, min_length=1)
    shop_cipher: SecretStr = Field(min_length=1)
    access_expires_at: AwareDatetime
    grant_expires_at: AwareDatetime
    verified_at: AwareDatetime
    scopes: frozenset[str]


class VerifiedFirstConnectResult(_Contract):
    """Trusted first-connect verifier output: the single shop the fresh lookup returned.

    Same trust rules as VerifiedAuthorizationResult; the shop comes only from the provider's
    fresh authorized-shops answer, never from the browser or the pending store.
    """

    context: TikTokFirstConnectContext
    shop_id: str = Field(min_length=1, max_length=100)
    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = Field(default=None, min_length=1)
    shop_cipher: SecretStr = Field(min_length=1)
    access_expires_at: AwareDatetime
    grant_expires_at: AwareDatetime
    verified_at: AwareDatetime
    scopes: frozenset[str]


async def _write_credential(
    conn: AsyncConnection[Any], path: str, params: dict[str, Any]
) -> dict[str, Any] | None:
    """Run a credential UPDATE; a shop key violation is shop_in_use and reveals no holder."""
    try:
        row: dict[str, Any] | None = await db.q.fetch_one(path, params, conn=conn)
    except UniqueViolation as exc:
        code = "shop_in_use" if exc.diag.constraint_name in _SHOP_KEYS else "credential_changed"
        raise TikTokAuthorizationError(code) from None
    return row


async def _fence(conn: AsyncConnection[Any], context: TikTokAuthorizationContext) -> None:
    await require_app(conn, context.app_id, context.app_revision)
    binding = context.binding
    row = await db.q.fetch_one(
        "ecommerce/lock_credential.sql",
        (binding.client_id, binding.record_id, binding.shop_id, context.credential_generation),
        conn=conn,
    )
    if row is None:
        raise TikTokAuthorizationError("credential_changed")


async def _fence_pending(conn: AsyncConnection[Any], context: TikTokFirstConnectContext) -> None:
    await require_app(conn, context.app_id, context.app_revision)
    binding = context.binding
    row = await db.q.fetch_one(
        "ecommerce/lock_pending_credential.sql",
        (binding.client_id, binding.record_id, context.credential_generation),
        conn=conn,
    )
    if row is None:
        raise TikTokAuthorizationError("credential_changed")


async def _create_attempt(
    conn: AsyncConnection[Any],
    context: TikTokAuthorizationContext | TikTokFirstConnectContext,
) -> StartedAuthorization:
    """Random single-use state; only its sha256 is stored. Caller holds the fences."""
    state = secrets.token_urlsafe(32)
    row = await db.q.fetch_one(
        "ecommerce/create_authorization_attempt.sql",
        (
            uuid4(),
            context.binding.client_id,
            Jsonb(context.model_dump(mode="json")),
            hashlib.sha256(state.encode()).hexdigest(),
        ),
        conn=conn,
    )
    attempt = AuthorizationAttempt.model_validate(row)
    if attempt.context != context:
        raise TikTokAuthorizationError("binding_mismatch")
    return StartedAuthorization(attempt=attempt, state=SecretStr(state))


async def begin_authorization(
    pool: AsyncConnectionPool, context: TikTokAuthorizationContext
) -> StartedAuthorization:
    """Server-selected existing local store only; caller must enforce staff authority."""
    async with tx(pool, str(context.binding.client_id)) as conn:
        await _fence(conn, context)
        return await _create_attempt(conn, context)


async def create_pending_store(conn: AsyncConnection[Any], client_id: UUID) -> UUID:
    """Insert a TikTok store with no shop, on the caller's tenant transaction.

    It is never readable: readers, grants and read operations all require a bound shop.
    Caller must enforce Admin authority; abandoned pending stores are purged later. A
    client_id other than the transaction's tenant writes nothing and is refused.
    """
    row = await db.q.fetch_one(
        "ecommerce/create_pending_store.sql", {"id": uuid4(), "client_id": client_id}, conn=conn
    )
    if row is None:
        raise TikTokAuthorizationError("binding_mismatch")
    record_id: UUID = row["id"]
    return record_id


async def begin_first_authorization(
    pool: AsyncConnectionPool, context: TikTokFirstConnectContext
) -> StartedAuthorization:
    """First connect for a server-created pending store; caller must enforce Admin authority.

    Same single-use attempt as a reconnect: random state stored only as a sha256, 10-minute
    lifetime, fenced on the app revision and on the pending row at its credential generation.
    """
    async with tx(pool, str(context.binding.client_id)) as conn:
        await _fence_pending(conn, context)
        return await _create_attempt(conn, context)


async def cancel_authorization(
    pool: AsyncConnectionPool, client_id: UUID, attempt_id: UUID
) -> None:
    """Server-authorized cancellation wins only before consumption; no credential change."""
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one(
            "ecommerce/cancel_authorization_attempt.sql", (client_id, attempt_id), conn=conn
        )
        if row is None:
            raise TikTokAuthorizationError("authorization_attempt_unavailable")


def _encrypt(
    value: SecretStr, column: str, record_id: UUID, encrypt: Callable[[str, bytes], str]
) -> str:
    try:
        encrypted = encrypt(
            value.get_secret_value(),
            credential_aad("channel_credentials", column, str(record_id)),
        )
    except (CryptoError, UnicodeError):
        raise TikTokAuthorizationError("credential_encryption_failed") from None
    if not encrypted.startswith("v1:") or len(encrypted) <= 3:
        raise TikTokAuthorizationError("unsupported_credential_encryption")
    return encrypted


async def complete_authorization(
    pool: AsyncConnectionPool,
    client_id: UUID,
    attempt_id: UUID,
    *,
    state: SecretStr,
    authorization_code: SecretStr,
    verify: Callable[
        [TikTokAuthorizationContext, SecretStr], Awaitable[VerifiedAuthorizationResult]
    ],
    encrypt: Callable[[str, bytes], str],
) -> TikTokGrantState:
    """Consume before one bounded injected exchange; uncertain outcomes never replay.

    No live verifier is supplied. It must be cancellation-cooperative, single-attempt,
    no automatic retry and raise safe errors without token/code/provider payload text.
    Failure leaves prior credentials intact and the attempt consumed; use a new attempt.
    """
    if not state.get_secret_value() or not authorization_code.get_secret_value():
        raise TikTokAuthorizationError("missing_authorization_input")
    hashed = hashlib.sha256(state.get_secret_value().encode()).hexdigest()
    async with asyncio.timeout(EXCHANGE_TIMEOUT_SECONDS):
        async with tx(pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "ecommerce/get_authorization_attempt.sql",
                (client_id, attempt_id, hashed),
                conn=conn,
            )
            if row is None:
                raise TikTokAuthorizationError("authorization_attempt_unavailable")
            attempt = AuthorizationAttempt.model_validate(row)
            # A first-connect attempt is refused here, before consumption; it has its own path.
            if not isinstance(attempt.context, TikTokAuthorizationContext):
                raise TikTokAuthorizationError("binding_mismatch")
            context = attempt.context
            if context.binding.client_id != client_id:
                raise TikTokAuthorizationError("binding_mismatch")
            await _fence(conn, context)
            consumed = await db.q.fetch_one(
                "ecommerce/consume_authorization_attempt.sql",
                (client_id, attempt_id, hashed),
                conn=conn,
            )
            if consumed is None:
                raise TikTokAuthorizationError("authorization_attempt_unavailable")
        # Never restore this consumed attempt, even if exchange or persistence fails.
        async with tx(pool, str(client_id)) as conn:
            await _fence(conn, context)
            if attempt.expires_at <= datetime.now(UTC):
                raise TikTokAuthorizationError("expired_authorization_attempt")
            result = await verify(context, authorization_code)
            now = datetime.now(UTC)
            if result.context != context:
                raise TikTokAuthorizationError("binding_mismatch")
            if not attempt.created_at <= result.verified_at <= now:
                raise TikTokAuthorizationError("invalid_grant_time")
            if result.access_expires_at <= now:
                raise TikTokAuthorizationError("expired_credential")
            next_context = context.model_copy(
                update={"credential_generation": context.credential_generation + 1}
            )
            grant = TikTokGrantState(
                id=uuid4(),
                context=next_context,
                revision=1,
                state="active",
                source="verified_authorization_exchange",
                scopes=result.scopes,
                verified_at=result.verified_at,
                expires_at=min(result.grant_expires_at, result.access_expires_at),
            )
            require_tiktok_grant(
                TikTokReadOperation(id=uuid4(), context=next_context, started_at=now),
                grant=grant,
                now=now,
            )
            binding = context.binding
            updated = await _write_credential(
                conn,
                "ecommerce/replace_authorized_credential.sql",
                {
                    "client_id": client_id,
                    "record_id": binding.record_id,
                    "shop_id": binding.shop_id,
                    "generation": context.credential_generation,
                    "access_token": _encrypt(
                        result.access_token, "access_token", binding.record_id, encrypt
                    ),
                    "refresh_token": (
                        _encrypt(result.refresh_token, "refresh_token", binding.record_id, encrypt)
                        if result.refresh_token is not None
                        else None
                    ),
                    "expires_at": result.access_expires_at,
                },
            )
            if (
                updated is None
                or updated["credential_generation"] != next_context.credential_generation
            ):
                raise TikTokAuthorizationError("credential_changed")
            await insert_verified_grant(conn, grant)
            await insert_verified_shop_binding(conn, grant, result.shop_cipher, encrypt=encrypt)
            completed = await db.q.fetch_one(
                "ecommerce/complete_authorization_attempt.sql",
                (grant.id, client_id, attempt_id),
                conn=conn,
            )
            if completed is None:
                raise TikTokAuthorizationError("expired_authorization_attempt")
            return grant


async def complete_first_authorization(
    pool: AsyncConnectionPool,
    client_id: UUID,
    attempt_id: UUID,
    *,
    state: SecretStr,
    authorization_code: SecretStr,
    verify: Callable[[TikTokFirstConnectContext, SecretStr], Awaitable[VerifiedFirstConnectResult]],
    encrypt: Callable[[str, bytes], str],
) -> TikTokGrantState:
    """First connect: burn the attempt, verify once, then bind the single shop atomically.

    Same contract as complete_authorization: the attempt is consumed before the one bounded
    exchange and never restored; failure binds nothing and leaves the pending store as it was.
    A shop already stored for any client fails as shop_in_use without naming the holder.
    """
    if not state.get_secret_value() or not authorization_code.get_secret_value():
        raise TikTokAuthorizationError("missing_authorization_input")
    hashed = hashlib.sha256(state.get_secret_value().encode()).hexdigest()
    async with asyncio.timeout(EXCHANGE_TIMEOUT_SECONDS):
        async with tx(pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "ecommerce/get_authorization_attempt.sql",
                (client_id, attempt_id, hashed),
                conn=conn,
            )
            if row is None:
                raise TikTokAuthorizationError("authorization_attempt_unavailable")
            attempt = AuthorizationAttempt.model_validate(row)
            # A reconnect attempt is refused here, before consumption; it has its own path.
            if not isinstance(attempt.context, TikTokFirstConnectContext):
                raise TikTokAuthorizationError("binding_mismatch")
            context = attempt.context
            if context.binding.client_id != client_id:
                raise TikTokAuthorizationError("binding_mismatch")
            await _fence_pending(conn, context)
            consumed = await db.q.fetch_one(
                "ecommerce/consume_authorization_attempt.sql",
                (client_id, attempt_id, hashed),
                conn=conn,
            )
            if consumed is None:
                raise TikTokAuthorizationError("authorization_attempt_unavailable")
        # Never restore this consumed attempt, even if exchange or persistence fails.
        async with tx(pool, str(client_id)) as conn:
            await _fence_pending(conn, context)
            if attempt.expires_at <= datetime.now(UTC):
                raise TikTokAuthorizationError("expired_authorization_attempt")
            result = await verify(context, authorization_code)
            now = datetime.now(UTC)
            if result.context != context:
                raise TikTokAuthorizationError("binding_mismatch")
            if not attempt.created_at <= result.verified_at <= now:
                raise TikTokAuthorizationError("invalid_grant_time")
            if result.access_expires_at <= now:
                raise TikTokAuthorizationError("expired_credential")
            require_tiktok_scopes(result.scopes)
            require_tiktok_product_scope(result.scopes)
            pending = context.binding
            try:
                binding = StoreBinding(
                    client_id=pending.client_id,
                    record_id=pending.record_id,
                    platform=pending.platform,
                    shop_id=result.shop_id,
                )
            except ValidationError:
                raise TikTokAuthorizationError("binding_mismatch") from None
            bound_context = TikTokAuthorizationContext(
                binding=binding,
                app_id=context.app_id,
                app_revision=context.app_revision,
                credential_generation=context.credential_generation + 1,
            )
            grant = TikTokGrantState(
                id=uuid4(),
                context=bound_context,
                revision=1,
                state="active",
                source="verified_authorization_exchange",
                scopes=result.scopes,
                verified_at=result.verified_at,
                expires_at=min(result.grant_expires_at, result.access_expires_at),
            )
            require_tiktok_grant(
                TikTokReadOperation(id=uuid4(), context=bound_context, started_at=now),
                grant=grant,
                now=now,
            )
            updated = await _write_credential(
                conn,
                "ecommerce/complete_first_credential.sql",
                {
                    "client_id": client_id,
                    "record_id": binding.record_id,
                    "shop_id": binding.shop_id,
                    "generation": context.credential_generation,
                    "access_token": _encrypt(
                        result.access_token, "access_token", binding.record_id, encrypt
                    ),
                    "refresh_token": (
                        _encrypt(result.refresh_token, "refresh_token", binding.record_id, encrypt)
                        if result.refresh_token is not None
                        else None
                    ),
                    "expires_at": result.access_expires_at,
                },
            )
            if (
                updated is None
                or updated["credential_generation"] != bound_context.credential_generation
            ):
                raise TikTokAuthorizationError("credential_changed")
            await insert_verified_grant(conn, grant)
            await insert_verified_shop_binding(conn, grant, result.shop_cipher, encrypt=encrypt)
            completed = await db.q.fetch_one(
                "ecommerce/complete_authorization_attempt.sql",
                (grant.id, client_id, attempt_id),
                conn=conn,
            )
            if completed is None:
                raise TikTokAuthorizationError("expired_authorization_attempt")
            return grant
