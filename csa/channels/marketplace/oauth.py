"""TikTok Shop and marketplace OAuth authorization and token lifecycle (LG13 / LG09)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import structlog
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from csa.core import crypto, db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

TIKTOK_AUTH_BASE_URL = "https://services.tiktokshop.com/open/authorize"
TIKTOK_TOKEN_API_URL = "https://auth.tiktok-shops.com/api/v2/token/get"


class OAuthError(Exception):
    """Raised when OAuth state validation or token exchange fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def create_oauth_state(client_id: UUID, secret: str, expires_in_secs: int = 900) -> str:
    """Create a tamper-proof signed state token containing client_id and TTL."""
    payload = {
        "client_id": str(client_id),
        "exp": int(time.time()) + expires_in_secs,
        "nonce": secrets.token_hex(8),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    sig = hmac.new(secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{encoded}.{sig}"


def verify_oauth_state(state: str, secret: str) -> UUID:
    """Verify state token signature and expiration, returning client_id."""
    parts = state.split(".", 1)
    if len(parts) != 2:
        raise OAuthError(400, "Invalid OAuth state format")
    encoded, sig = parts
    expected_sig = hmac.new(
        secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        raise OAuthError(400, "Invalid OAuth state signature")
    try:
        data = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise OAuthError(400, "Malformed OAuth state payload") from exc

    if not isinstance(data, dict) or "client_id" not in data or "exp" not in data:
        raise OAuthError(400, "Incomplete OAuth state payload")

    if time.time() > float(data["exp"]):
        raise OAuthError(400, "OAuth state has expired")

    try:
        return UUID(str(data["client_id"]))
    except ValueError as exc:
        raise OAuthError(400, "Invalid client_id in OAuth state") from exc


def build_tiktok_shop_auth_url(
    service_id: str,
    state: str,
    redirect_uri: str | None = None,
) -> str:
    """Build the TikTok Shop seller authorization URL."""
    url = f"{TIKTOK_AUTH_BASE_URL}?service_id={service_id}&state={state}"
    if redirect_uri:
        import urllib.parse

        url += f"&redirect_uri={urllib.parse.quote_plus(redirect_uri)}"
    return url


async def exchange_tiktok_shop_token(
    app_key: str,
    app_secret: str,
    auth_code: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for TikTok Shop tokens."""
    params = {
        "app_key": app_key,
        "app_secret": app_secret,
        "auth_code": auth_code,
        "grant_type": "authorized_code",
    }
    client = http_client or httpx.AsyncClient(timeout=15.0)
    owns_client = http_client is None
    try:
        resp = await client.get(TIKTOK_TOKEN_API_URL, params=params)
        resp.raise_for_status()
        result = resp.json()
    except httpx.HTTPError as exc:
        logger.error("tiktok_token_exchange_http_failed", error=str(exc))
        raise OAuthError(502, f"Failed to communicate with TikTok Shop API: {exc}") from exc
    finally:
        if owns_client:
            await client.aclose()

    code = result.get("code")
    if code != 0:
        msg = result.get("message", "Unknown TikTok Shop error")
        logger.error("tiktok_token_exchange_rejected", code=code, message=msg)
        raise OAuthError(400, f"TikTok Shop token exchange failed: {msg} (code {code})")

    data = result.get("data")
    if not isinstance(data, dict):
        raise OAuthError(502, "TikTok Shop returned unexpected token payload")

    return data


async def save_tiktok_shop_credentials(
    pool: AsyncConnectionPool,
    *,
    client_id: UUID,
    token_data: dict[str, Any],
) -> tuple[UUID, str]:
    """Persist encrypted TikTok Shop credentials and ensure webhook endpoint is registered."""
    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    if not access_token or not isinstance(access_token, str):
        raise OAuthError(502, "access_token missing in TikTok Shop response")

    shop_id = str(token_data.get("open_id") or token_data.get("seller_name") or "default_shop")
    seller_name = str(token_data.get("seller_name") or f"TikTok Shop ({shop_id})")
    access_expire_in = int(token_data.get("access_token_expire_in", 604800))
    refresh_expire_in = int(token_data.get("refresh_token_expire_in", 2592000))

    expires_at = datetime.now(UTC) + timedelta(seconds=access_expire_in)
    meta = {
        "seller_name": seller_name,
        "open_id": token_data.get("open_id"),
        "seller_base_region": token_data.get("seller_base_region", "MY"),
        "user_type": token_data.get("user_type"),
        "refresh_token_expires_at": (
            datetime.now(UTC) + timedelta(seconds=refresh_expire_in)
        ).isoformat(),
    }

    async with tx(pool, str(client_id)) as conn:
        existing = await db.q.fetch_one(
            "channels/get_credential.sql",
            (client_id, "tiktok_shop", shop_id),
            conn=conn,
        )
        if existing is not None:
            cred_id = existing["id"]
        else:
            cred_id = uuid4()

        aad_access = crypto.credential_aad("channel_credentials", "access_token", str(cred_id))
        aad_refresh = crypto.credential_aad("channel_credentials", "refresh_token", str(cred_id))
        access_token_enc = crypto.encrypt(access_token, aad_access)
        refresh_token_enc = (
            crypto.encrypt(refresh_token, aad_refresh)
            if isinstance(refresh_token, str) and refresh_token
            else None
        )

        if existing is not None:
            await db.q.fetch_one(
                "channels/update_credential.sql",
                (access_token_enc, refresh_token_enc, expires_at, Jsonb(meta), cred_id, client_id),
                conn=conn,
            )
        else:
            await db.q.fetch_one(
                "channels/insert_credential.sql",
                (
                    cred_id,
                    client_id,
                    "tiktok_shop",
                    shop_id,
                    access_token_enc,
                    refresh_token_enc,
                    expires_at,
                    Jsonb(meta),
                ),
                conn=conn,
            )

        # Ensure webhook endpoint exists with public_id = shop_id
        ep_existing = await db.q.fetch_one(
            "channels/get_endpoint_by_public_id.sql",
            (shop_id,),
            conn=conn,
        )
        if ep_existing is not None:
            await db.q.fetch_one(
                "channels/update_endpoint.sql",
                (cred_id, f"TikTok Shop - {seller_name}", shop_id, client_id),
                conn=conn,
            )
        else:
            ep_id = uuid4()
            await db.q.fetch_one(
                "channels/insert_endpoint.sql",
                (ep_id, client_id, shop_id, cred_id, f"TikTok Shop - {seller_name}"),
                conn=conn,
            )

    logger.info(
        "tiktok_shop_credentials_saved",
        client_id=str(client_id),
        shop_id=shop_id,
        seller_name=seller_name,
    )
    return cred_id, shop_id
