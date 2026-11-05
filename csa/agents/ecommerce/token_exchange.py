"""Single-attempt TikTok token exchange with no client hooks or URL logging."""

import asyncio
import json
from datetime import datetime

import httpx
from pydantic import SecretStr

from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.token_response import (
    TikTokTokenResponse,
    parse_tiktok_token_response,
)

TOKEN_ENDPOINT = "https://auth.tiktok-shops.com/api/v2/token/get"
EXCHANGE_TIMEOUT_SECONDS = 15
MAX_RESPONSE_BYTES = 64 * 1024


def _new_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=True)


async def _read_response(response: httpx.Response, prefix: str) -> bytes:
    if response.status_code != 200:
        raise TikTokAuthorizationError(f"{prefix}_http_rejected")
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise TikTokAuthorizationError(f"{prefix}_invalid_response")
    content_length = response.headers.get("content-length")
    if content_length is not None:
        try:
            length = int(content_length)
            if length < 0:
                raise ValueError
            if length > MAX_RESPONSE_BYTES:
                raise TikTokAuthorizationError(f"{prefix}_response_too_large")
        except ValueError:
            raise TikTokAuthorizationError(f"{prefix}_invalid_response") from None

    body = bytearray()
    async for chunk in response.aiter_raw(chunk_size=8192):
        body.extend(chunk)
        if len(body) > MAX_RESPONSE_BYTES:
            raise TikTokAuthorizationError(f"{prefix}_response_too_large")
    return bytes(body)


async def send_token_request_once(
    request: httpx.Request, transport: httpx.AsyncBaseTransport, *, prefix: str
) -> object:
    """Send one token request with no retry, bounded body and fixed ``<prefix>_*`` codes.

    The URL carries secrets, so it never reaches client hooks, logs or exception text.
    """
    try:
        async with asyncio.timeout(EXCHANGE_TIMEOUT_SECONDS):
            try:
                response = await transport.handle_async_request(request)
                try:
                    body = await _read_response(response, prefix)
                finally:
                    await response.aclose()
            except httpx.HTTPError:
                raise TikTokAuthorizationError(f"{prefix}_transport_failed") from None
    except TimeoutError:
        raise TikTokAuthorizationError(f"{prefix}_timeout") from None
    except httpx.HTTPError:
        raise TikTokAuthorizationError(f"{prefix}_transport_failed") from None
    finally:
        try:
            await transport.aclose()
        except httpx.HTTPError:
            raise TikTokAuthorizationError(f"{prefix}_transport_failed") from None

    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise TikTokAuthorizationError(f"{prefix}_invalid_response") from None


async def exchange_tiktok_authorization_code(
    *,
    app_key: SecretStr,
    app_secret: SecretStr,
    authorization_code: SecretStr,
    now: datetime,
) -> TikTokTokenResponse:
    """Exchange once; query credentials never pass through client hooks or telemetry."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise TikTokAuthorizationError("invalid_clock")
    if any(not value.get_secret_value() for value in (app_key, app_secret, authorization_code)):
        raise TikTokAuthorizationError("missing_authorization_input")

    request = httpx.Request(
        "GET",
        TOKEN_ENDPOINT,
        params=[
            ("app_key", app_key.get_secret_value()),
            ("app_secret", app_secret.get_secret_value()),
            ("auth_code", authorization_code.get_secret_value()),
            ("grant_type", "authorized_code"),
        ],
        headers={"accept": "application/json", "accept-encoding": "identity"},
    )
    payload = await send_token_request_once(request, _new_transport(), prefix="token_exchange")
    return parse_tiktok_token_response(payload, now=now)
