"""Trusted exchange storage and transaction-bound loading; no runtime registration."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from csa.agents.ecommerce.apps import require_app
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokGrantState,
)
from csa.agents.ecommerce.reader import BoundTikTokReader
from csa.agents.ecommerce.resolver import (
    TikTokAppMaterial,
    TikTokCredentialMaterial,
    TikTokCredentialResolver,
)
from csa.core import db
from csa.core.crypto import CryptoError, credential_aad
from csa.core.tenancy import tx


class _StoredMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    grant_id: UUID
    shop_cipher: SecretStr = Field(min_length=1)
    access_token_ciphertext: SecretStr = Field(min_length=1)
    expires_at: AwareDatetime


async def create_verified_shop_binding(
    pool: AsyncConnectionPool,
    grant: TikTokGrantState,
    shop_cipher: SecretStr,
    *,
    encrypt: Callable[[str, bytes], str],
) -> None:
    """Trusted exchange output only, not an API or proof of provider provenance.

    A grant without this evidence remains unreadable. No rebind/overwrite is supported.
    The caller must authenticate the exchange and app mapping before invoking this.
    """
    if not shop_cipher.get_secret_value():
        raise TikTokAuthorizationError("empty_shop_cipher")
    context = grant.context
    binding = context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        await insert_verified_shop_binding(conn, grant, shop_cipher, encrypt=encrypt)


async def insert_verified_shop_binding(
    conn: AsyncConnection[Any],
    grant: TikTokGrantState,
    shop_cipher: SecretStr,
    *,
    encrypt: Callable[[str, bytes], str],
) -> None:
    """Same trusted writer on caller-owned transaction; never commit independently."""
    if not shop_cipher.get_secret_value():
        raise TikTokAuthorizationError("empty_shop_cipher")
    context = grant.context
    binding = context.binding
    await require_app(conn, context.app_id, context.app_revision)
    credential = await db.q.fetch_one(
        "ecommerce/lock_credential.sql",
        (binding.client_id, binding.record_id, binding.shop_id, context.credential_generation),
        conn=conn,
    )
    if credential is None:
        raise TikTokAuthorizationError("credential_changed")
    await db.q.fetch_one("ecommerce/lock_grant.sql", (binding.client_id, grant.id), conn=conn)
    row = await db.q.fetch_one("ecommerce/get_grant.sql", (binding.client_id, grant.id), conn=conn)
    now = datetime.now(UTC)
    if (
        row is None
        or TikTokGrantState.model_validate(row) != grant
        or grant.state != "active"
        or not grant.verified_at <= now < grant.expires_at
    ):
        raise TikTokAuthorizationError("shop_binding_grant_mismatch")
    try:
        encrypted = encrypt(
            shop_cipher.get_secret_value(),
            credential_aad("ecommerce_shop_bindings", "shop_cipher", str(grant.id)),
        )
    except (CryptoError, UnicodeError):
        raise TikTokAuthorizationError("shop_cipher_encryption_failed") from None
    if not encrypted.startswith("v1:") or len(encrypted) <= 3:
        raise TikTokAuthorizationError("unsupported_credential_encryption")
    created = await db.q.fetch_one(
        "ecommerce/create_shop_binding.sql",
        (binding.client_id, grant.id, encrypted),
        conn=conn,
    )
    if created is None:
        raise TikTokAuthorizationError("shop_binding_conflict")


class TikTokStoredCredentialResolver:
    """Explicit external app registry and crypto, using the dispatch-owned transaction.

    No pool acquisition, settings/key lookup, refresh or legacy metadata inference.
    Global app revision is fenced before credential/grant. External snapshots must
    be immutable by revision; disable must commit before changing external material.
    """

    def __init__(
        self,
        *,
        load_app: Callable[[str], Awaitable[TikTokAppMaterial | None]],
        decrypt: Callable[[str, bytes], str],
        http_client: httpx.AsyncClient,
    ) -> None:
        self._load_app = load_app
        self._decrypt = decrypt
        self._http = http_client

    async def __call__(
        self, context: TikTokAuthorizationContext, conn: AsyncConnection[Any]
    ) -> BoundTikTokReader:
        await require_app(conn, context.app_id, context.app_revision)

        async def load_credential(requested: TikTokAuthorizationContext):
            binding = requested.binding
            row = await db.q.fetch_one(
                "ecommerce/get_credential_material.sql",
                {
                    "client_id": binding.client_id,
                    "record_id": binding.record_id,
                    "shop_id": binding.shop_id,
                    "credential_generation": requested.credential_generation,
                    "app_id": requested.app_id,
                    "app_revision": requested.app_revision,
                },
                conn=conn,
            )
            if row is None:
                raise TikTokAuthorizationError("credential_binding_unavailable")
            stored = _StoredMaterial.model_validate(row)
            encrypted = stored.shop_cipher.get_secret_value()
            if not encrypted.startswith("v1:"):
                raise TikTokAuthorizationError("unsupported_credential_encryption")
            try:
                cipher = self._decrypt(
                    encrypted,
                    credential_aad("ecommerce_shop_bindings", "shop_cipher", str(stored.grant_id)),
                )
            except (CryptoError, UnicodeError):
                raise TikTokAuthorizationError("shop_cipher_decryption_failed") from None
            if not cipher:
                raise TikTokAuthorizationError("empty_shop_cipher")
            return TikTokCredentialMaterial(
                context=requested,
                expires_at=stored.expires_at,
                access_token_ciphertext=stored.access_token_ciphertext,
                shop_cipher=SecretStr(cipher),
            )

        resolver = TikTokCredentialResolver(
            load_app=self._load_app,
            load_credential=load_credential,
            decrypt=self._decrypt,
            http_client=self._http,
        )
        return await resolver(context)
