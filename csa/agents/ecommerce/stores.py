"""Local store identities only; these contracts never authorize provider access."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from csa.agents.ecommerce.models import MarketplacePlatform


class StoreBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_id: UUID
    record_id: UUID
    platform: MarketplacePlatform
    shop_id: str = Field(min_length=1, max_length=100)


class PendingStoreBinding(BaseModel):
    """A TikTok store waiting for its first approval: no shop yet, so never readable.

    Kept apart from StoreBinding on purpose. Grants, read operations and every reader take
    only StoreBinding, so a pending store cannot be read until a first connect binds a shop.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    client_id: UUID
    record_id: UUID
    platform: Literal["tiktok_shop"]


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
    # "available" once the grant is verified and recorded (provider reads can run), else
    # "unavailable". Derived from status: a caller cannot claim more than the lifecycle shows.
    live_integration: Literal["available", "unavailable"] = "unavailable"

    @model_validator(mode="before")
    @classmethod
    def _derive_live_integration(cls, data: Any) -> Any:
        if isinstance(data, dict) and "live_integration" not in data and "status" in data:
            return {**data, "live_integration": _availability(data["status"])}
        return data

    @model_validator(mode="after")
    def _live_integration_matches_status(self) -> "StoreConnection":
        if self.live_integration != _availability(self.status):
            raise ValueError("live_integration must follow the connection status")
        return self


class StoreSync(BaseModel):
    """Sync progress of one TikTok store; ``problem`` is set while a failure notice is open."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    orders_synced_at: AwareDatetime | None = None
    products_synced_at: AwareDatetime | None = None
    problem: Literal["sync_failing"] | None = None


class StoreRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    platform: MarketplacePlatform
    shop_id: str | None
    expires_at: AwareDatetime | None
    updated_at: AwareDatetime
    expiry: Literal["unknown", "expired", "not_expired"]
    # The three summary fields follow connection.status (see _derive and _consistent).
    authorization: Literal["verified", "unverified"] = "unverified"
    permissions: Literal["granted", "missing", "unknown"] = "unknown"
    provider_reads: Literal["available", "unavailable"] = "unavailable"
    connection: StoreConnection = Field(
        default_factory=lambda: StoreConnection(status="unverified")
    )
    sync: StoreSync | None = None
    # True for platform admins and for the Admin of this tenant, never for staff.
    can_manage: bool = False

    @model_validator(mode="before")
    @classmethod
    def _derive(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        connection = data.get("connection")
        if isinstance(connection, StoreConnection):
            status: Any = connection.status
        elif isinstance(connection, dict):
            status = connection.get("status")
        else:
            return data
        derived = _summary(status)
        return {**{k: v for k, v in derived.items() if k not in data}, **data}

    @model_validator(mode="after")
    def _consistent(self) -> "StoreRecord":
        derived = _summary(self.connection.status)
        if (self.authorization, self.permissions, self.provider_reads) != (
            derived["authorization"],
            derived["permissions"],
            derived["provider_reads"],
        ):
            raise ValueError("store summary must follow the connection status")
        return self


def _availability(status: Any) -> Literal["available", "unavailable"]:
    return "available" if status == "authorization_recorded" else "unavailable"


def _summary(status: Any) -> dict[str, str]:
    recorded = status == "authorization_recorded"
    return {
        "authorization": "verified" if recorded else "unverified",
        "permissions": "granted"
        if recorded
        else "missing"
        if status == "permissions_missing"
        else "unknown",
        "provider_reads": _availability(status),
    }


def expiry_state(
    expires_at: datetime | None, *, now: datetime
) -> Literal["unknown", "expired", "not_expired"]:
    if now.tzinfo is None or (expires_at is not None and expires_at.tzinfo is None):
        raise ValueError("store expiry evaluation requires timezone-aware timestamps")
    if expires_at is None:
        return "unknown"
    return "expired" if expires_at <= now else "not_expired"
