"""Multi-marketplace ingress and OAuth callback router (TikTok Shop, Shopee, Lazada)."""

from csa.channels.marketplace.ingress import MarketplaceWebhookReceipt, TikTokWebhookIngress
from csa.channels.marketplace.oauth import (
    OAuthError,
    build_tiktok_shop_auth_url,
    create_oauth_state,
    exchange_tiktok_shop_token,
    save_tiktok_shop_credentials,
    verify_oauth_state,
)
from csa.channels.marketplace.router import build_marketplace_router

__all__ = [
    "MarketplaceWebhookReceipt",
    "OAuthError",
    "TikTokWebhookIngress",
    "build_marketplace_router",
    "build_tiktok_shop_auth_url",
    "create_oauth_state",
    "exchange_tiktok_shop_token",
    "save_tiktok_shop_credentials",
    "verify_oauth_state",
]
