"""Selected token-response parsing only; no transport or proof of app/shop binding."""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
)

from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationError,
    require_tiktok_scopes,
)

_MAX_UNIX_SECONDS = int(datetime.max.replace(tzinfo=UTC, microsecond=0).timestamp())
_UnixSeconds = Annotated[int, Field(strict=True, ge=1, le=_MAX_UNIX_SECONDS)]


class _TokenData(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    access_token_expire_in: _UnixSeconds
    refresh_token_expire_in: _UnixSeconds
    user_type: int = Field(strict=True)
    granted_scopes: list[Annotated[str, Field(strict=True, min_length=1)]] = Field(strict=True)

    @field_validator("granted_scopes")
    @classmethod
    def distinct_exact_scopes(cls, scopes: list[str]) -> list[str]:
        if len(set(scopes)) != len(scopes) or any(scope != scope.strip() for scope in scopes):
            raise ValueError("invalid scope keys")
        return scopes


class _Envelope(BaseModel):
    model_config = ConfigDict(extra="ignore", hide_input_in_errors=True)

    code: int = Field(strict=True)
    data: object = None


class TikTokTokenResponse(BaseModel):
    """Selected exchange data, not VerifiedAuthorizationResult or usable authorization.

    No tenant/app/shop/cipher is inferred. Refresh expiry remains explicit, separate
    from access expiry; this model implements neither refresh nor token persistence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    access_token: SecretStr = Field(min_length=1)
    refresh_token: SecretStr = Field(min_length=1)
    access_expires_at: AwareDatetime
    refresh_expires_at: AwareDatetime
    scopes: frozenset[str]


def parse_tiktok_token_response(payload: object, *, now: datetime) -> TikTokTokenResponse:
    """Parse documented absolute seconds, rejecting unsafe/unsupported data with fixed codes."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise TikTokAuthorizationError("invalid_clock")
    try:
        envelope = _Envelope.model_validate(payload)
    except ValidationError:
        raise TikTokAuthorizationError("invalid_token_response") from None
    if envelope.code != 0:
        raise TikTokAuthorizationError("token_exchange_rejected")
    try:
        data = _TokenData.model_validate(envelope.data)
    except ValidationError:
        raise TikTokAuthorizationError("invalid_token_response") from None
    if data.user_type != 0:
        raise TikTokAuthorizationError("unsupported_token_user_type")
    access_expiry = datetime.fromtimestamp(data.access_token_expire_in, UTC)
    refresh_expiry = datetime.fromtimestamp(data.refresh_token_expire_in, UTC)
    if access_expiry <= now or refresh_expiry <= now:
        raise TikTokAuthorizationError("expired_exchange_token")
    scopes = frozenset(data.granted_scopes)
    require_tiktok_scopes(scopes)
    return TikTokTokenResponse(
        access_token=data.access_token,
        refresh_token=data.refresh_token,
        access_expires_at=access_expiry,
        refresh_expires_at=refresh_expiry,
        scopes=scopes,
    )
