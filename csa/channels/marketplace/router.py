"""FastAPI router for multi-marketplace ingress and OAuth callbacks (LG13 / LG09)."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from psycopg_pool import AsyncConnectionPool

from csa.channels.ingress import IngressError
from csa.channels.marketplace.ingress import TikTokWebhookIngress
from csa.channels.marketplace.oauth import (
    OAuthError,
    build_tiktok_shop_auth_url,
    create_oauth_state,
    exchange_tiktok_shop_token,
    save_tiktok_shop_credentials,
    verify_oauth_state,
)
from csa.core.settings import get_settings

logger = structlog.get_logger(__name__)

_MAX_INGRESS_BYTES = 1_000_000


def build_marketplace_router(pool: AsyncConnectionPool | None = None) -> APIRouter:
    """Build and configure the marketplace ingress APIRouter."""
    router = APIRouter(prefix="/api/v1/channels", tags=["marketplace"])

    def _get_pool(request: Request) -> AsyncConnectionPool:
        if pool is not None:
            return pool
        return request.app.state.pool

    # --------------------------------------------------------------------------
    # TikTok Shop Webhook Ingress
    # --------------------------------------------------------------------------

    async def _handle_tiktok_webhook(
        request: Request,
        public_id: str | None = None,
    ) -> JSONResponse:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            return JSONResponse(
                status_code=415,
                content={"code": 41501, "message": "expected application/json"},
            )

        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > _MAX_INGRESS_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"code": 41301, "message": "request body is too large"},
                )
            chunks.append(chunk)

        body = b"".join(chunks)
        signature = (
            request.headers.get("authorization")
            or request.headers.get("x-tts-signature")
            or request.query_params.get("sign")
        )

        settings = get_settings()
        app_secret = (
            settings.TIKTOK_APP_SECRET.get_secret_value() if settings.TIKTOK_APP_SECRET else None
        )
        current_pool = _get_pool(request)
        ingress = TikTokWebhookIngress(current_pool, app_secret=app_secret)

        try:
            receipt = await ingress.receive(
                raw_body=body,
                signature=signature,
                public_id=public_id,
            )
        except IngressError as exc:
            if exc.status_code >= 500:
                logger.error(
                    "tiktok_webhook_ingress_internal_error",
                    status_code=exc.status_code,
                    detail=exc.detail,
                )
            return JSONResponse(
                status_code=exc.status_code,
                content={"code": exc.status_code, "message": exc.detail},
            )

        # Standard TikTok Shop webhook acknowledgment
        return JSONResponse(
            status_code=200,
            content={
                "code": 0,
                "message": "success",
                "data": {
                    "trace_id": str(receipt.trace_id),
                    "duplicate": receipt.duplicate,
                },
            },
        )

    @router.post("/tiktok_shop/webhook")
    async def tiktok_shop_webhook(request: Request) -> JSONResponse:
        """Global webhook ingress for TikTok Shop events (order, product, returns)."""
        return await _handle_tiktok_webhook(request)

    @router.post("/tiktok_shop/webhook/{public_id}")
    async def tiktok_shop_endpoint_webhook(public_id: str, request: Request) -> JSONResponse:
        """Endpoint-scoped webhook ingress for TikTok Shop events."""
        return await _handle_tiktok_webhook(request, public_id=public_id)

    # --------------------------------------------------------------------------
    # TikTok Shop OAuth Endpoints
    # --------------------------------------------------------------------------

    @router.get("/tiktok_shop/oauth/authorize")
    async def tiktok_shop_oauth_authorize(
        client_id: Annotated[UUID, Query(description="Tenant client ID")],
        redirect_uri: Annotated[str | None, Query(description="Custom redirect URI")] = None,
        json_response: Annotated[
            bool, Query(description="Return JSON instead of redirect")
        ] = False,
    ) -> Any:
        """Generate signed state and redirect seller to TikTok Shop OAuth consent."""
        settings = get_settings()
        if not settings.TIKTOK_APP_KEY:
            raise HTTPException(
                status_code=503,
                detail="TIKTOK_APP_KEY is not configured in environment",
            )

        secret = settings.CREDENTIALS_KEY.get_secret_value()
        state = create_oauth_state(client_id, secret)

        base_url = str(settings.PUBLIC_BASE_URL).rstrip("/")
        effective_redirect = (
            redirect_uri or f"{base_url}/api/v1/channels/tiktok_shop/oauth/callback"
        )
        auth_url = build_tiktok_shop_auth_url(
            service_id=settings.TIKTOK_APP_KEY,
            state=state,
            redirect_uri=effective_redirect,
        )

        if json_response:
            return {"authorization_url": auth_url, "state": state}
        return RedirectResponse(auth_url, status_code=302)

    @router.get("/tiktok_shop/oauth/callback")
    async def tiktok_shop_oauth_callback(
        request: Request,
        code: Annotated[str, Query(description="Authorization code from TikTok Shop")],
        state: Annotated[str, Query(description="Signed OAuth state parameter")],
    ) -> Any:
        """Handle TikTok Shop OAuth callback, exchange tokens, and securely persist them."""
        settings = get_settings()
        secret = settings.CREDENTIALS_KEY.get_secret_value()

        try:
            client_id = verify_oauth_state(state, secret)
        except OAuthError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        if not settings.TIKTOK_APP_KEY or not settings.TIKTOK_APP_SECRET:
            return JSONResponse(
                status_code=503,
                content={"detail": "TikTok Shop App credentials not configured on server"},
            )

        current_pool = _get_pool(request)
        try:
            token_data = await exchange_tiktok_shop_token(
                app_key=settings.TIKTOK_APP_KEY,
                app_secret=settings.TIKTOK_APP_SECRET.get_secret_value(),
                auth_code=code,
            )
            cred_id, shop_id = await save_tiktok_shop_credentials(
                current_pool,
                client_id=client_id,
                token_data=token_data,
            )
        except OAuthError as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        except Exception as exc:
            logger.error("tiktok_oauth_save_failed", error=str(exc))
            return JSONResponse(
                status_code=500,
                content={"detail": f"Failed to save credentials: {exc}"},
            )

        seller_name = token_data.get("seller_name", shop_id)
        wants_json = "application/json" in request.headers.get("accept", "")
        if wants_json:
            return {
                "ok": True,
                "status": "connected",
                "client_id": str(client_id),
                "credential_id": str(cred_id),
                "shop_id": shop_id,
                "seller_name": seller_name,
            }

        html = f"""<!DOCTYPE html>
<html>
<head><title>TikTok Shop Connected</title>
<style>
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  display: flex; justify-content: center; align-items: center; height: 100vh;
  margin: 0; background-color: #f7f9fa;
}}
.card {{
  background: white; padding: 2.5rem; border-radius: 12px;
  box-shadow: 0 4px 12px rgba(0,0,0,0.08); text-align: center; max-width: 440px;
}}
h1 {{ color: #10b981; font-size: 1.5rem; margin-bottom: 0.5rem; }}
p {{ color: #4b5563; line-height: 1.5; }}
.badge {{
  display: inline-block; background: #e0f2fe; color: #0369a1;
  padding: 0.25rem 0.75rem; border-radius: 9999px; font-size: 0.875rem;
  font-weight: 600; margin-top: 1rem;
}}
</style>
</head>
<body>
<div class="card">
  <h1>Connection Successful!</h1>
  <p>TikTok Shop <strong>{seller_name}</strong> is now connected to your CSA platform.</p>
  <div class="badge">Shop ID: {shop_id}</div>
</div>
</body>
</html>"""
        return HTMLResponse(content=html, status_code=200)

    # --------------------------------------------------------------------------
    # Shopee & Lazada Draft Placeholders (LG13 / LG09 Draft)
    # --------------------------------------------------------------------------

    @router.post("/shopee/webhook")
    async def shopee_webhook() -> JSONResponse:
        """Draft ingress endpoint for Shopee Open Platform webhooks."""
        return JSONResponse(
            status_code=501,
            content={
                "code": 501,
                "platform": "shopee",
                "message": "Shopee marketplace webhook is drafted. Awaiting partner registration.",
            },
        )

    @router.get("/shopee/oauth/callback")
    async def shopee_oauth_callback() -> JSONResponse:
        """Draft OAuth callback endpoint for Shopee Open Platform."""
        return JSONResponse(
            status_code=501,
            content={
                "code": 501,
                "platform": "shopee",
                "message": "Shopee OAuth callback is drafted. Awaiting partner registration.",
            },
        )

    @router.post("/lazada/webhook")
    async def lazada_webhook() -> JSONResponse:
        """Draft ingress endpoint for Lazada Open Platform webhooks."""
        return JSONResponse(
            status_code=501,
            content={
                "code": 501,
                "platform": "lazada",
                "message": "Lazada marketplace webhook is drafted. Awaiting partner registration.",
            },
        )

    @router.get("/lazada/oauth/callback")
    async def lazada_oauth_callback() -> JSONResponse:
        """Draft OAuth callback endpoint for Lazada Open Platform."""
        return JSONResponse(
            status_code=501,
            content={
                "code": 501,
                "platform": "lazada",
                "message": "Lazada OAuth callback is drafted. Awaiting partner registration.",
            },
        )

    return router
