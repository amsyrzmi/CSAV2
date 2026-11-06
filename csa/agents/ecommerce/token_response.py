"""Selected token-response parsing only; no transport or proof of app/shop binding.

The code exchange and the token refresh answer with the same envelope and token fields; only
a refresh may omit ``granted_scopes`` (the caller then carries the previous grant's over).
"""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
)

from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationError,
    require_tiktok_scopes,
)

_MAX_UNIX_SECONDS = int(datetime.max.replace(tzinfo=UTC, microsecond=0).timestamp())
_UnixSeconds = Annotated[int, Field(strict=True, ge=1, le=_MAX_UNIX_SECONDS)]


def _exact_scopes(scopes: list[str]) -> list[str]:
    if len(set(scopes)) != len(scopes) or any(scope != scope.strip() for scope in scopes):
        raise ValueError("invalid scope keys")
    return scopes


_Scopes = Annotated[
    list[Annotated[str, Field(strict=True, min_length=1)]], AfterValidator(_exact_scopes)
]


class _TokenFields(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    access_token_expire_in: _UnixSeconds
    refresh_token_expire_in: _UnixSeconds
    user_type: int = Field(strict=True)


class _TokenData(_TokenFields):
    granted_scopes: _Scopes = Field(strict=True)


class _RefreshData(_TokenFields):
    """Same fields as the exchange, except that the scope list may be absent (or null)."""

    granted_scopes: _Scopes | None = Field(default=None, strict=True)


class TikTokProviderRejection(TikTokAuthorizationError):
    """The provider answered with a non-zero envelope code.

    ``provider_code`` is that integer only, kept for the caller's classification; it is not
    in the message, and the provider's message text is never kept anywhere.
    """

    def __init__(self, code: str, *, provider_code: int) -> None:
        super().__init__(code)
        self.provider_code = provider_code


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    code: int = Field(strict=True)
    data: object = None


class TikTokTokenResponse(BaseModel):
    """Selected exchange or refresh data, not VerifiedAuthorizationResult or usable authorization.

    No tenant/app/shop/cipher is inferred. Refresh expiry remains explicit, separate
    from access expiry; this model implements no transport or token persistence.
    ``scopes`` is empty only when a refresh response omitted them; an exchange always has them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    access_expires_at: AwareDatetime
    refresh_expires_at: AwareDatetime
    scopes: frozenset[str]


def _parse[DataT: _TokenFields](
    payload: object, *, now: datetime, data_model: type[DataT], rejected: str
) -> tuple[DataT, datetime, datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise TikTokAuthorizationError("invalid_clock")
    try:
        envelope = _Envelope.model_validate(payload)
    except ValidationError:
        raise TikTokAuthorizationError("invalid_token_response") from None
    if envelope.code != 0:
        raise TikTokProviderRejection(rejected, provider_code=envelope.code)
    try:
        data = data_model.model_validate(envelope.data)
    except ValidationError:
        raise TikTokAuthorizationError("invalid_token_response") from None
    if data.user_type != 0:
        raise TikTokAuthorizationError("unsupported_token_user_type")
    access_expiry = datetime.fromtimestamp(data.access_token_expire_in, UTC)
    refresh_expiry = datetime.fromtimestamp(data.refresh_token_expire_in, UTC)
    if access_expiry <= now or refresh_expiry <= now:
        raise TikTokAuthorizationError("expired_exchange_token")
    return data, access_expiry, refresh_expiry


def parse_tiktok_token_response(payload: object, *, now: datetime) -> TikTokTokenResponse:
    """Parse documented absolute seconds, rejecting unsafe/unsupported data with fixed codes."""
    data, access_expiry, refresh_expiry = _parse(
        payload, now=now, data_model=_TokenData, rejected="token_exchange_rejected"
    )
    scopes = frozenset(data.granted_scopes)
    require_tiktok_scopes(scopes)
    return TikTokTokenResponse(
        access_token=data.access_token,
        refresh_token=data.refresh_token,
        access_expires_at=access_expiry,
        refresh_expires_at=refresh_expiry,
        scopes=scopes,
    )


def parse_tiktok_refresh_response(payload: object, *, now: datetime) -> TikTokTokenResponse:
    """Parse a refresh answer: same rules as the exchange, rejection is ``token_refresh_rejected``.

    The refresh token is required, so a rotated one is always stored. Scopes, when present, are
    exact and must cover orders and the shop (an explicit empty list is ``insufficient_scope``);
    when absent the result carries an empty set and the caller keeps the previous grant's scopes.
    """
    data, access_expiry, refresh_expiry = _parse(
        payload, now=now, data_model=_RefreshData, rejected="token_refresh_rejected"
    )
    scopes = frozenset(data.granted_scopes) if data.granted_scopes is not None else frozenset()
    if data.granted_scopes is not None:
        require_tiktok_scopes(scopes)
    return TikTokTokenResponse(
        access_token=data.access_token,
        refresh_token=data.refresh_token,
        access_expires_at=access_expiry,
        refresh_expires_at=refresh_expiry,
        scopes=scopes,
    )
