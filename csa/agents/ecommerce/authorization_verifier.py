"""Trusted composition of code exchange, app revision and exact authorized-shop proof."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from csa.agents.ecommerce.adapters.tiktok_shop import TikTokReadError, TikTokShopAdapter
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
)
from csa.agents.ecommerce.connections import VerifiedAuthorizationResult
from csa.agents.ecommerce.models import TikTokShopCredentials
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.token_exchange import (
    EXCHANGE_TIMEOUT_SECONDS,
    exchange_tiktok_authorization_code,
)

_VERIFY_TIMEOUT_SECONDS = EXCHANGE_TIMEOUT_SECONDS


def _new_shop_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=True)


class TikTokAuthorizationVerifier:
    """Verifier for `complete_authorization`; callers must hold the app/attempt fences.

    The app loader must return an immutable server-owned app-key/secret snapshot for
    the requested revision. No browser-supplied app material or shop identity is used.
    """

    def __init__(
        self,
        *,
        load_app: Callable[[str], Awaitable[TikTokAppMaterial | None]],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._load_app = load_app
        self._clock = clock

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise TikTokAuthorizationError("invalid_clock")
        return now

    async def __call__(
        self, context: TikTokAuthorizationContext, authorization_code: SecretStr
    ) -> VerifiedAuthorizationResult:
        async with asyncio.timeout(_VERIFY_TIMEOUT_SECONDS):
            app = await self._load_app(context.app_id)
            if (
                app is None
                or not app.enabled
                or app.app_id != context.app_id
                or app.revision != context.app_revision
            ):
                raise TikTokAuthorizationError("app_binding_unavailable")

            token_response = await exchange_tiktok_authorization_code(
                app_key=app.app_key,
                app_secret=app.app_secret,
                authorization_code=authorization_code,
                now=self._now(),
            )
            credentials = TikTokShopCredentials(
                app_key=app.app_key.get_secret_value(),
                app_secret=app.app_secret.get_secret_value(),
                access_token=token_response.access_token.get_secret_value(),
                shop_id=context.binding.shop_id,
            )
            transport = _new_shop_transport()
            try:
                adapter = TikTokShopAdapter(credentials, http_transport=transport)
                try:
                    shop_cipher = await adapter.authorized_shop_cipher(context.binding.shop_id)
                except TikTokReadError:
                    raise TikTokAuthorizationError("authorized_shop_verification_failed") from None
            finally:
                try:
                    await transport.aclose()
                except httpx.HTTPError:
                    raise TikTokAuthorizationError("authorized_shop_verification_failed") from None

            verified_at = self._now()
            if (
                token_response.access_expires_at <= verified_at
                or token_response.refresh_expires_at <= verified_at
            ):
                raise TikTokAuthorizationError("expired_exchange_token")
            return VerifiedAuthorizationResult(
                context=context,
                access_token=token_response.access_token,
                refresh_token=token_response.refresh_token,
                shop_cipher=shop_cipher,
                access_expires_at=token_response.access_expires_at,
                grant_expires_at=token_response.access_expires_at,
                verified_at=verified_at,
                scopes=token_response.scopes,
            )
