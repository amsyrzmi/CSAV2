"""Token refresh as a verified event (spec 6.2), the sanctioned extension of M6B.

A refresh is treated like a reconnect without the browser: refresh once, prove with the new
access token that the stored shop (and its cipher) is still authorized, then in the same
transaction replace the tokens, which bumps the generation and ends the old grant, and record
a new ``verified_token_refresh`` grant with a new shop binding.

- TikTok refused the refresh with a known refusal code (REFRESH_REFUSAL_CODES), the refreshed
  scopes no longer cover orders and the shop, or the shop no longer matches: the store is marked
  expired (tokens cleared, grant ended) and waits for a reconnect. Nothing is retried.
- A lost or unusable response (transport, timeout, HTTP status, malformed body, a non-zero
  code that is not a known refusal, shop lookup failure): a safe ``TikTokAuthorizationError``
  is raised, nothing is written, the old credential keeps working until it expires and the
  next run tries again.
- Nothing to do (pending, disconnected, revoked or expired store, TikTok off, or the store
  changed under us): ``"skipped"`` with no provider call.

The whole refresh holds the credential row lock at its generation, so two workers refreshing
one store call TikTok once: the second waits, then finds the generation moved and skips.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from csa.agents.ecommerce.adapters.tiktok_shop import TikTokReadError, TikTokShopAdapter
from csa.agents.ecommerce.app_loader import AppLoader
from csa.agents.ecommerce.apps import require_app
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokGrantState,
    TikTokReadOperation,
    require_tiktok_grant,
    require_tiktok_scopes,
)
from csa.agents.ecommerce.connections import _encrypt
from csa.agents.ecommerce.grants import insert_verified_grant
from csa.agents.ecommerce.models import TikTokShopCredentials
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.storage import insert_verified_shop_binding
from csa.agents.ecommerce.token_refresh import refresh_tiktok_token
from csa.agents.ecommerce.token_response import TikTokProviderRejection
from csa.core import db
from csa.core.crypto import CryptoError, credential_aad
from csa.core.tenancy import tx

RefreshOutcome = Literal["refreshed", "expired", "skipped"]

# Refresh access tokens that expire within this window (spec 6.2: the next 24 hours).
REFRESH_DUE_WITHIN = timedelta(hours=24)
# One bound for the refresh call and the authorized-shops call together, like the verifier's.
REFRESH_TIMEOUT_SECONDS = 15
# PLACEHOLDER, deliberately empty: the provider codes that mean "this refresh token is invalid or
# expired". They are read in Partner Center and filled in at rollout (ledger, Task 21). Only a
# code listed here marks the store expired. Any other non-zero code (rate limit, internal error,
# anything unknown) is a lost response: nothing is written and the next run tries again, so a
# transient TikTok error can never clear the tokens of healthy stores. A store whose access
# token lapses meanwhile still reads 'expired' through expires_at.
REFRESH_REFUSAL_CODES: frozenset[int] = frozenset()
# The fresh authorized-shops answer no longer has the stored shop, or its cipher changed.
_SHOP_MISMATCH = frozenset({"shop_not_authorized", "shop_binding_mismatch"})


def _new_shop_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=True)


class _Source(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    shop_id: str = Field(min_length=1, max_length=100)
    credential_generation: int = Field(strict=True, ge=1)
    refresh_token_ciphertext: SecretStr = Field(min_length=1)
    grant_id: UUID
    shop_cipher_ciphertext: SecretStr = Field(min_length=1)


def _is_refusal(exc: TikTokAuthorizationError) -> bool:
    """A listed refusal code, or a successful refresh whose scopes no longer cover orders and the
    shop (a verified answer, like a shop mismatch). Everything else counts as lost."""
    if exc.code == "insufficient_scope":
        return True
    return (
        isinstance(exc, TikTokProviderRejection)
        and exc.code == "token_refresh_rejected"
        and exc.provider_code in REFRESH_REFUSAL_CODES
    )


def _aware_now(clock: Callable[[], datetime]) -> datetime:
    now = clock()
    if now.tzinfo is None or now.utcoffset() is None:
        raise TikTokAuthorizationError("invalid_clock")
    return now


def _decrypt(
    decrypt: Callable[[str, bytes], str], value: SecretStr, aad: bytes, code: str
) -> SecretStr:
    encrypted = value.get_secret_value()
    if not encrypted.startswith("v1:"):
        raise TikTokAuthorizationError("unsupported_credential_encryption")
    try:
        plain = decrypt(encrypted, aad)
    except (CryptoError, UnicodeError):
        raise TikTokAuthorizationError(code) from None
    if not plain:
        raise TikTokAuthorizationError("empty_credential")
    return SecretStr(plain)


async def list_refresh_due(
    conn: AsyncConnection[Any], client_id: UUID, *, within: timedelta
) -> list[UUID]:
    """Connected stores whose access token expires within ``within`` or whose grant is older
    than the app's current revision, on the caller's tenant transaction.

    Grants are immutable, so after an app secret rotation (disable, swap, enable) the refresh
    records a new grant at the new revision: connections survive it without a reconnect.
    """
    if within < timedelta(0):
        raise TikTokAuthorizationError("invalid_refresh_window")
    rows = await db.q.fetch_all(
        "ecommerce/list_refresh_due.sql", {"client_id": client_id, "within": within}, conn=conn
    )
    return [row["id"] for row in rows]


async def _lock_source(
    conn: AsyncConnection[Any], client_id: UUID, store_id: UUID, load_app: AppLoader
) -> tuple[_Source, TikTokGrantState, TikTokAppMaterial] | None:
    """Read the store, then fence app, credential and grant in that order; None means skip."""
    row = await db.q.fetch_one(
        "ecommerce/get_refresh_source.sql",
        {"client_id": client_id, "record_id": store_id},
        conn=conn,
    )
    if row is None:
        return None
    try:
        source = _Source.model_validate(row)
    except ValidationError:
        raise TikTokAuthorizationError("credential_binding_unavailable") from None
    grant_row = await db.q.fetch_one(
        "ecommerce/get_grant.sql", (client_id, source.grant_id), conn=conn
    )
    if grant_row is None:
        return None
    old = TikTokGrantState.model_validate(grant_row)
    app = await load_app(old.context.app_id)
    if app is None or not app.enabled or app.app_id != old.context.app_id:
        return None
    try:
        await require_app(conn, app.app_id, app.revision)
    except TikTokAuthorizationError:
        return None
    binding = old.context.binding
    locked = await db.q.fetch_one(
        "ecommerce/lock_credential.sql",
        (client_id, store_id, source.shop_id, source.credential_generation),
        conn=conn,
    )
    if locked is None:
        return None
    await db.q.fetch_one("ecommerce/lock_grant.sql", (client_id, old.id), conn=conn)
    current = await db.q.fetch_one("ecommerce/get_grant.sql", (client_id, old.id), conn=conn)
    if (
        current is None
        or TikTokGrantState.model_validate(current) != old
        or old.state != "active"
        or binding.client_id != client_id
        or binding.record_id != store_id
        or binding.shop_id != source.shop_id
        or old.context.credential_generation != source.credential_generation
    ):
        return None
    return source, old, app


async def _shop_still_matches(
    app: TikTokAppMaterial, access_token: SecretStr, shop_id: str, shop_cipher: SecretStr
) -> bool:
    """Fresh authorized shops with the new token: the exact stored shop and cipher, or False."""
    credentials = TikTokShopCredentials(
        app_key=app.app_key.get_secret_value(),
        app_secret=app.app_secret.get_secret_value(),
        access_token=access_token.get_secret_value(),
        shop_id=shop_id,
        shop_cipher=shop_cipher.get_secret_value(),
    )
    transport = _new_shop_transport()
    try:
        adapter = TikTokShopAdapter(credentials, http_transport=transport)
        try:
            await adapter.require_authorized_shop(shop_id)
        except TikTokReadError as exc:
            if exc.code in _SHOP_MISMATCH:
                return False
            raise TikTokAuthorizationError("authorized_shop_verification_failed") from None
    finally:
        try:
            await transport.aclose()
        except httpx.HTTPError:
            raise TikTokAuthorizationError("authorized_shop_verification_failed") from None
    return True


async def _mark_expired(conn: AsyncConnection[Any], old: TikTokGrantState) -> RefreshOutcome:
    binding = old.context.binding
    row = await db.q.fetch_one(
        "ecommerce/mark_store_expired.sql",
        {
            "client_id": binding.client_id,
            "record_id": binding.record_id,
            "shop_id": binding.shop_id,
            "generation": old.context.credential_generation,
        },
        conn=conn,
    )
    if row is None or row["credential_generation"] != old.context.credential_generation + 1:
        raise TikTokAuthorizationError("credential_changed")
    return "expired"


async def refresh_store(
    pool: AsyncConnectionPool,
    client_id: UUID,
    store_id: UUID,
    *,
    load_app: AppLoader,
    encrypt: Callable[[str, bytes], str],
    decrypt: Callable[[str, bytes], str],
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> RefreshOutcome:
    """Refresh one store's tokens once and re-verify its shop; see the module docstring."""
    async with tx(pool, str(client_id)) as conn:
        locked = await _lock_source(conn, client_id, store_id, load_app)
        if locked is None:
            return "skipped"
        source, old, app = locked
        binding = old.context.binding
        refresh_token = _decrypt(
            decrypt,
            source.refresh_token_ciphertext,
            credential_aad("channel_credentials", "refresh_token", str(store_id)),
            "credential_decryption_failed",
        )
        shop_cipher = _decrypt(
            decrypt,
            source.shop_cipher_ciphertext,
            credential_aad("ecommerce_shop_bindings", "shop_cipher", str(old.id)),
            "shop_cipher_decryption_failed",
        )
        try:
            async with asyncio.timeout(REFRESH_TIMEOUT_SECONDS):
                token = await refresh_tiktok_token(
                    app_key=app.app_key,
                    app_secret=app.app_secret,
                    refresh_token=refresh_token,
                    now=_aware_now(clock),
                )
                matches = await _shop_still_matches(
                    app, token.access_token, binding.shop_id, shop_cipher
                )
        except TimeoutError:
            raise TikTokAuthorizationError("token_refresh_timeout") from None
        except TikTokAuthorizationError as exc:
            if _is_refusal(exc):
                return await _mark_expired(conn, old)
            raise
        if not matches:
            return await _mark_expired(conn, old)

        now = _aware_now(clock)
        if token.access_expires_at <= now or token.refresh_expires_at <= now:
            raise TikTokAuthorizationError("expired_exchange_token")
        # Scopes from the response; a refresh that omits them keeps the previous grant's.
        scopes = token.scopes or old.scopes
        require_tiktok_scopes(scopes)
        next_context = TikTokAuthorizationContext(
            binding=binding,
            app_id=app.app_id,
            app_revision=app.revision,
            credential_generation=old.context.credential_generation + 1,
        )
        grant = TikTokGrantState(
            id=uuid4(),
            context=next_context,
            revision=1,
            state="active",
            source="verified_token_refresh",
            scopes=scopes,
            verified_at=now,
            expires_at=min(token.access_expires_at, token.refresh_expires_at),
        )
        require_tiktok_grant(
            TikTokReadOperation(id=uuid4(), context=next_context, started_at=now),
            grant=grant,
            now=now,
        )
        updated = await db.q.fetch_one(
            "ecommerce/refresh_credential.sql",
            {
                "client_id": client_id,
                "record_id": store_id,
                "shop_id": binding.shop_id,
                "generation": old.context.credential_generation,
                "access_token": _encrypt(token.access_token, "access_token", store_id, encrypt),
                "refresh_token": _encrypt(token.refresh_token, "refresh_token", store_id, encrypt),
                "expires_at": token.access_expires_at,
            },
            conn=conn,
        )
        if (
            updated is None
            or updated["credential_generation"] != next_context.credential_generation
        ):
            raise TikTokAuthorizationError("credential_changed")
        await insert_verified_grant(conn, grant)
        await insert_verified_shop_binding(conn, grant, shop_cipher, encrypt=encrypt)
        return "refreshed"
