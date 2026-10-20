"""Local store identities only; these contracts never authorize provider access."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from csa.agents.ecommerce.models import MarketplacePlatform


class StoreBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_id: UUID
    record_id: UUID
    platform: MarketplacePlatform
    shop_id: str = Field(min_length=1, max_length=100)


class StoreConnection(BaseModel):
    """Local lifecycle observation, never a current provider authorization decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal[
        "unsupported",
        "missing_shop_id",
        "unverified",
        "revoked",
        "disconnected",
        "expired",
        "app_unavailable",
        "binding_missing",
        "permissions_missing",
        "authorization_recorded",
    ]
    live_integration: Literal["unavailable"] = "unavailable"


class StoreRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    platform: MarketplacePlatform
    shop_id: str | None
    expires_at: AwareDatetime | None
    updated_at: AwareDatetime
    expiry: Literal["unknown", "expired", "not_expired"]
    authorization: Literal["unverified"] = "unverified"
    permissions: Literal["unknown"] = "unknown"
    provider_reads: Literal["unavailable"] = "unavailable"
    connection: StoreConnection = Field(
        default_factory=lambda: StoreConnection(status="unverified")
    )


def expiry_state(
    expires_at: datetime | None, *, now: datetime
) -> Literal["unknown", "expired", "not_expired"]:
    if now.tzinfo is None or (expires_at is not None and expires_at.tzinfo is None):
        raise ValueError("store expiry evaluation requires timezone-aware timestamps")
    if expires_at is None:
        return "unknown"
    return "expired" if expires_at <= now else "not_expired"
