"""FastAPI router for the marketplace webhooks (LG13 / LG09).

The TikTok connect and callback live in the console router (`csa/console/tiktok_connect.py`).
The TikTok webhook only verifies, resolves and queues (`csa/marketplace/webhook.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from csa.core.settings import get_settings
from csa.marketplace.disconnect import disconnect_store
from csa.marketplace.jobs import enqueue_job
from csa.marketplace.webhook import MAX_WEBHOOK_BYTES, TikTokWebhookHandler, WebhookRejected


def _refusal(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": status_code, "message": code})


def build_marketplace_router(pool: AsyncConnectionPool | None = None) -> APIRouter:
    """Build and configure the marketplace webhook APIRouter."""
    router = APIRouter(prefix="/api/v1/channels", tags=["marketplace"])

    def _get_pool(request: Request) -> AsyncConnectionPool:
        if pool is not None:
            return pool
        return request.app.state.pool

    # --------------------------------------------------------------------------
    # TikTok Shop webhook
    # --------------------------------------------------------------------------

    async def _handle_tiktok_webhook(request: Request) -> JSONResponse:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type != "application/json":
            return JSONResponse(
                status_code=415,
                content={"code": 41501, "message": "expected application/json"},
            )

        # TikTok off (spec 6.1): the webhook answers 404 and reads nothing.
        settings = get_settings()
        secret = settings.TIKTOK_APP_SECRET
        app_key = settings.TIKTOK_APP_KEY
        if not settings.tiktok_enabled or secret is None or not app_key:
            return _refusal(404, "not_found")

        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_WEBHOOK_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"code": 41301, "message": "request body is too large"},
                )
            chunks.append(chunk)

        signature = (
            request.headers.get("authorization")
            or request.headers.get("x-tts-signature")
            or request.query_params.get("sign")
        )
        handler = TikTokWebhookHandler(
            _get_pool(request),
            secret.get_secret_value(),
            app_key,
            enqueue_job=enqueue_job,
            disconnect_store=disconnect_store,
        )
        try:
            await handler.receive(raw_body=b"".join(chunks), signature=signature)
        except WebhookRejected as exc:
            return _refusal(exc.status_code, exc.code)
        # TikTok's acknowledgement shape; nothing about the event is echoed back.
        return JSONResponse(status_code=200, content={"code": 0, "message": "success"})

    @router.post("/tiktok_shop/webhook")
    async def tiktok_shop_webhook(request: Request) -> JSONResponse:
        """TikTok Shop webhook: order, product and deauthorization events."""
        return await _handle_tiktok_webhook(request)

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
