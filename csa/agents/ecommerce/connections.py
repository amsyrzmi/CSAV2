"""Offline authorization choreography; no provider exchange, callback route or key source."""

import asyncio
import hashlib
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from csa.agents.ecommerce.apps import require_app
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokGrantState,
    TikTokReadOperation,
    require_tiktok_grant,
)
from csa.agents.ecommerce.grants import insert_verified_grant
from csa.agents.ecommerce.storage import insert_verified_shop_binding
from csa.core import db
from csa.core.crypto import CryptoError, credential_aad
from csa.core.tenancy import tx

EXCHANGE_TIMEOUT_SECONDS = 15


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class AuthorizationAttempt(_Contract):
    id: UUID
    context: TikTokAuthorizationContext
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


async def begin_authorization(
    pool: AsyncConnectionPool, context: TikTokAuthorizationContext
) -> StartedAuthorization:
    """Server-selected existing local store only; caller must enforce staff authority."""
    state = secrets.token_urlsafe(32)
    async with tx(pool, str(context.binding.client_id)) as conn:
        await _fence(conn, context)
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
        return StartedAuthorization(
            attempt=AuthorizationAttempt.model_validate(row), state=SecretStr(state)
        )


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
            if attempt.context.binding.client_id != client_id:
                raise TikTokAuthorizationError("binding_mismatch")
            await _fence(conn, attempt.context)
            consumed = await db.q.fetch_one(
                "ecommerce/consume_authorization_attempt.sql",
                (client_id, attempt_id, hashed),
                conn=conn,
            )
            if consumed is None:
                raise TikTokAuthorizationError("authorization_attempt_unavailable")
        # Never restore this consumed attempt, even if exchange or persistence fails.
        async with tx(pool, str(client_id)) as conn:
            await _fence(conn, attempt.context)
            if attempt.expires_at <= datetime.now(UTC):
                raise TikTokAuthorizationError("expired_authorization_attempt")
            result = await verify(attempt.context, authorization_code)
            now = datetime.now(UTC)
            if result.context != attempt.context:
                raise TikTokAuthorizationError("binding_mismatch")
            if not attempt.created_at <= result.verified_at <= now:
                raise TikTokAuthorizationError("invalid_grant_time")
            if result.access_expires_at <= now:
                raise TikTokAuthorizationError("expired_credential")
            next_context = attempt.context.model_copy(
                update={"credential_generation": attempt.context.credential_generation + 1}
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
            binding = attempt.context.binding
            updated = await db.q.fetch_one(
                "ecommerce/replace_authorized_credential.sql",
                {
                    "client_id": client_id,
                    "record_id": binding.record_id,
                    "shop_id": binding.shop_id,
                    "generation": attempt.context.credential_generation,
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
                conn=conn,
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
