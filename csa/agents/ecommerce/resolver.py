"""Offline-capable resolver composition; no default storage, keys or HTTP transport."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr

from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
)
from csa.agents.ecommerce.models import TikTokShopCredentials
from csa.agents.ecommerce.reader import BoundTikTokReader
from csa.core.crypto import CryptoError, credential_aad

if TYPE_CHECKING:
    from psycopg import AsyncConnection


class _ResolverContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class TikTokAppMaterial(_ResolverContract):
    """Trusted server registry output; never infer this mapping from credential meta."""

    app_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(strict=True, ge=1)
    app_key: SecretStr = Field(min_length=1)
    app_secret: SecretStr = Field(min_length=1)
    enabled: bool = Field(strict=True)


class TikTokCredentialMaterial(_ResolverContract):
    """Trusted exact-row projection, not arbitrary browser input or a full DB row.

    The shop cipher must come from verified app/shop binding evidence; this contract
    does not prescribe a storage location or accept arbitrary legacy meta as authority.
    """

    context: TikTokAuthorizationContext
    expires_at: AwareDatetime
    access_token_ciphertext: SecretStr = Field(min_length=1)
    shop_cipher: SecretStr = Field(min_length=1)


class TikTokCredentialResolver:
    """Injected server loaders and decryptor, called inside the reader's existing fences.

    Loaders must use that same coherent fenced state, not reacquire conflicting row
    locks through a second connection. No production loader or key source is supplied.
    The optional connection is accepted for reader compatibility; injected loaders
    remain caller-owned. Use the stored resolver for transaction-bound database loading.
    """

    def __init__(
        self,
        *,
        load_app: Callable[[str], Awaitable[TikTokAppMaterial | None]],
        load_credential: Callable[
            [TikTokAuthorizationContext], Awaitable[TikTokCredentialMaterial | None]
        ],
        decrypt: Callable[[str, bytes], str],
        http_client: httpx.AsyncClient,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._load_app = load_app
        self._load_credential = load_credential
        self._decrypt = decrypt
        self._http = http_client
        self._clock = clock

    def _require_unexpired(self, material: TikTokCredentialMaterial) -> None:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TikTokAuthorizationError("invalid_clock")
        if material.expires_at <= now:
            raise TikTokAuthorizationError("expired_credential")

    async def __call__(
        self, context: TikTokAuthorizationContext, conn: AsyncConnection[Any] | None = None
    ) -> BoundTikTokReader:
        app = await self._load_app(context.app_id)
        if (
            app is None
            or not app.enabled
            or app.app_id != context.app_id
            or app.revision != context.app_revision
        ):
            raise TikTokAuthorizationError("app_binding_unavailable")
        material = await self._load_credential(context)
        if material is None:
            raise TikTokAuthorizationError("missing_credential")
        if material.context != context:
            raise TikTokAuthorizationError("credential_binding_mismatch")
        self._require_unexpired(material)
        encrypted = material.access_token_ciphertext.get_secret_value()
        if not encrypted.startswith("v1:"):
            raise TikTokAuthorizationError("unsupported_credential_encryption")
        aad = credential_aad("channel_credentials", "access_token", str(context.binding.record_id))
        try:
            token = self._decrypt(encrypted, aad)
        except (CryptoError, UnicodeError):
            raise TikTokAuthorizationError("credential_decryption_failed") from None
        if not token:
            raise TikTokAuthorizationError("empty_credential")
        self._require_unexpired(material)
        return BoundTikTokReader(
            context,
            TikTokShopCredentials(
                app_key=app.app_key.get_secret_value(),
                app_secret=app.app_secret.get_secret_value(),
                access_token=token,
                shop_id=context.binding.shop_id,
                shop_cipher=material.shop_cipher.get_secret_value(),
            ),
            expected_app_key=app.app_key.get_secret_value(),
            http_client=self._http,
        )
