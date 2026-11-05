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
    TikTokFirstConnectContext,
    require_tiktok_product_scope,
    require_tiktok_scopes,
)
from csa.agents.ecommerce.connections import (
    VerifiedAuthorizationResult,
    VerifiedFirstConnectResult,
)
from csa.agents.ecommerce.models import TikTokShopCredentials
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.token_exchange import (
    EXCHANGE_TIMEOUT_SECONDS,
    exchange_tiktok_authorization_code,
)
from csa.agents.ecommerce.token_response import TikTokTokenResponse

_VERIFY_TIMEOUT_SECONDS = EXCHANGE_TIMEOUT_SECONDS


def _new_shop_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=True)


def _require_pinned_app(
    app: TikTokAppMaterial | None, context: TikTokAuthorizationContext | TikTokFirstConnectContext
) -> TikTokAppMaterial:
    if (
        app is None
        or not app.enabled
        or app.app_id != context.app_id
        or app.revision != context.app_revision
    ):
        raise TikTokAuthorizationError("app_binding_unavailable")
    return app


def _require_live_tokens(token_response: TikTokTokenResponse, verified_at: datetime) -> None:
    if (
        token_response.access_expires_at <= verified_at
        or token_response.refresh_expires_at <= verified_at
    ):
        raise TikTokAuthorizationError("expired_exchange_token")


class _PinnedAppVerifier:
    """Server-owned app loader and an aware clock, shared by both verifiers."""

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


class TikTokAuthorizationVerifier(_PinnedAppVerifier):
    """Verifier for `complete_authorization`; callers must hold the app/attempt fences.

    The app loader must return an immutable server-owned app-key/secret snapshot for
    the requested revision. No browser-supplied app material or shop identity is used.
    """

    async def __call__(
        self, context: TikTokAuthorizationContext, authorization_code: SecretStr
    ) -> VerifiedAuthorizationResult:
        async with asyncio.timeout(_VERIFY_TIMEOUT_SECONDS):
            app = _require_pinned_app(await self._load_app(context.app_id), context)

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
            _require_live_tokens(token_response, verified_at)
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


class TikTokFirstConnectVerifier(_PinnedAppVerifier):
    """Verifier for `complete_first_authorization`; callers must hold the app/pending fences.

    Exchanges the code once, checks the order, shop and product permissions, then fetches the
    authorized shops fresh and accepts exactly one. The shop comes only from that answer.
    """

    async def __call__(
        self, context: TikTokFirstConnectContext, authorization_code: SecretStr
    ) -> VerifiedFirstConnectResult:
        async with asyncio.timeout(_VERIFY_TIMEOUT_SECONDS):
            app = _require_pinned_app(await self._load_app(context.app_id), context)
            token_response = await exchange_tiktok_authorization_code(
                app_key=app.app_key,
                app_secret=app.app_secret,
                authorization_code=authorization_code,
                now=self._now(),
            )
            require_tiktok_scopes(token_response.scopes)
            require_tiktok_product_scope(token_response.scopes)
            credentials = TikTokShopCredentials(
                app_key=app.app_key.get_secret_value(),
                app_secret=app.app_secret.get_secret_value(),
                access_token=token_response.access_token.get_secret_value(),
            )
            transport = _new_shop_transport()
            try:
                adapter = TikTokShopAdapter(credentials, http_transport=transport)
                try:
                    shops = await adapter.authorized_shops()
                except TikTokReadError:
                    raise TikTokAuthorizationError("authorized_shop_verification_failed") from None
            finally:
                try:
                    await transport.aclose()
                except httpx.HTTPError:
                    raise TikTokAuthorizationError("authorized_shop_verification_failed") from None
            # Exactly one shop; choosing among several is deferred (spec 6.2).
            if len(shops) != 1:
                raise TikTokAuthorizationError("multiple_shops" if shops else "no_authorized_shop")
            (shop,) = shops

            verified_at = self._now()
            _require_live_tokens(token_response, verified_at)
            return VerifiedFirstConnectResult(
                context=context,
                shop_id=shop.id,
                access_token=token_response.access_token,
                refresh_token=token_response.refresh_token,
                shop_cipher=shop.cipher,
                access_expires_at=token_response.access_expires_at,
                grant_expires_at=token_response.access_expires_at,
                verified_at=verified_at,
                scopes=token_response.scopes,
            )
