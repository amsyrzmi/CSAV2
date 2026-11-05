"""Single-attempt TikTok token refresh with no client hooks or URL logging.

Same transport rules as the code exchange (token_exchange.py): one request, zero retries, no
proxy environment, a bounded identity-encoded body and fixed safe codes. The URL carries the
app secret and the refresh token, so it is never logged or put in exception text.
"""

from datetime import datetime

import httpx
from pydantic import SecretStr

from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.token_exchange import send_token_request_once
from csa.agents.ecommerce.token_response import (
    TikTokTokenResponse,
    parse_tiktok_refresh_response,
)

# --- Refresh contract: PLACEHOLDER until checked in Partner Center (spec 6.2) ---------------
# Written from TikTok Shop's public authorization docs, the same way the exchange contract was:
# GET on the auth host, credentials in the query string, an envelope of {code, message, data}
# with the exchange's token fields. Once checked, the ledger in docs/PROGRESS.md records it;
# change the endpoint and parameter names here only.
REFRESH_ENDPOINT = "https://auth.tiktok-shops.com/api/v2/token/refresh"
REFRESH_PARAM_APP_KEY = "app_key"
REFRESH_PARAM_APP_SECRET = "app_secret"
REFRESH_PARAM_REFRESH_TOKEN = "refresh_token"
REFRESH_PARAM_GRANT_TYPE = "grant_type"
REFRESH_GRANT_TYPE = "refresh_token"
# ---------------------------------------------------------------------------------------------


def _new_transport() -> httpx.AsyncHTTPTransport:
    return httpx.AsyncHTTPTransport(retries=0, trust_env=False, verify=True)


async def refresh_tiktok_token(
    *,
    app_key: SecretStr,
    app_secret: SecretStr,
    refresh_token: SecretStr,
    now: datetime,
) -> TikTokTokenResponse:
    """Refresh once. A provider refusal is ``token_refresh_rejected``; nothing is retried.

    Every other failure (transport, timeout, HTTP status, malformed body) carries its own
    ``token_refresh_*`` or parser code, so a caller can tell a refusal from a lost response.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise TikTokAuthorizationError("invalid_clock")
    if any(not value.get_secret_value() for value in (app_key, app_secret, refresh_token)):
        raise TikTokAuthorizationError("missing_authorization_input")

    request = httpx.Request(
        "GET",
        REFRESH_ENDPOINT,
        params=[
            (REFRESH_PARAM_APP_KEY, app_key.get_secret_value()),
            (REFRESH_PARAM_APP_SECRET, app_secret.get_secret_value()),
            (REFRESH_PARAM_REFRESH_TOKEN, refresh_token.get_secret_value()),
            (REFRESH_PARAM_GRANT_TYPE, REFRESH_GRANT_TYPE),
        ],
        headers={"accept": "application/json", "accept-encoding": "identity"},
    )
    payload = await send_token_request_once(request, _new_transport(), prefix="token_refresh")
    return parse_tiktok_refresh_response(payload, now=now)
