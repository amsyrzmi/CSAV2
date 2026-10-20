"""Internal evidence checks only; no credential resolution or live authorization."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from csa.agents.ecommerce.stores import StoreBinding

_ORDER_SCOPES = frozenset({"seller.order.info", "seller.fs.order&fufillment.management"})
_SHOP_SCOPES = frozenset({"seller.authorization.info", "seller.fs.order&fufillment.management"})


class TikTokAuthorizationError(RuntimeError):
    """Fixed safe codes, without grant contents or credential identifiers."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"TikTok authorization evidence rejected: {code}")


def require_tiktok_scopes(scopes: frozenset[str]) -> None:
    if not (scopes & _ORDER_SCOPES and scopes & _SHOP_SCOPES):
        raise TikTokAuthorizationError("insufficient_scope")


class _EvidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class TikTokAuthorizationContext(_EvidenceContract):
    binding: StoreBinding
    app_id: str = Field(min_length=1, max_length=100)
    app_revision: int = Field(default=1, strict=True, ge=1)
    credential_generation: int = Field(strict=True, ge=1)

    @model_validator(mode="after")
    def require_tiktok_binding(self) -> TikTokAuthorizationContext:
        if self.binding.platform != "tiktok_shop":
            raise ValueError("TikTok authorization requires a TikTok store binding")
        return self


class TikTokGrantState(_EvidenceContract):
    """Trusted durable state must be loaded afresh, never accepted from a browser."""

    id: UUID
    context: TikTokAuthorizationContext
    revision: int = Field(strict=True, ge=1)
    state: Literal["active", "revoked", "disconnected"]
    source: Literal["verified_authorization_exchange"]
    scopes: frozenset[str]
    verified_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def require_expiry_after_verification(self) -> TikTokGrantState:
        if self.expires_at <= self.verified_at:
            raise ValueError("grant expiry must follow verification")
        return self


class TikTokReadOperation(_EvidenceContract):
    """Server-created logical operation; each page read needs a new operation."""

    id: UUID
    context: TikTokAuthorizationContext
    started_at: AwareDatetime


class TikTokOperationEvidence(_EvidenceContract):
    """A future trusted verifier's result, not proof supplied by a client."""

    operation_id: UUID
    context: TikTokAuthorizationContext
    grant_id: UUID
    grant_revision: int = Field(strict=True, ge=1)
    source: Literal[
        "verified_authorization_and_authorized_shops",
        "fresh_authorized_shop_with_stored_grant_scopes",
    ]
    verified_at: AwareDatetime


def require_tiktok_grant(
    operation: TikTokReadOperation,
    *,
    grant: TikTokGrantState | None,
    now: datetime,
) -> None:
    """Validate current trusted grant metadata before any verification request."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise TikTokAuthorizationError("invalid_clock")
    if operation.started_at > now:
        raise TikTokAuthorizationError("invalid_clock")
    if grant is None:
        raise TikTokAuthorizationError("missing_grant")
    if grant.context != operation.context:
        raise TikTokAuthorizationError("binding_mismatch")
    if grant.state != "active":
        raise TikTokAuthorizationError("inactive_grant")
    if grant.verified_at > now or grant.verified_at > operation.started_at:
        raise TikTokAuthorizationError("invalid_grant_time")
    if grant.expires_at <= now:
        raise TikTokAuthorizationError("expired_grant")
    require_tiktok_scopes(grant.scopes)


def require_tiktok_order_read(
    operation: TikTokReadOperation,
    *,
    grant: TikTokGrantState | None,
    evidence: TikTokOperationEvidence | None,
    now: datetime,
) -> None:
    """Validate metadata and operation evidence, not freshly introspected order scopes."""
    require_tiktok_grant(operation, grant=grant, now=now)
    assert grant is not None
    if evidence is None:
        raise TikTokAuthorizationError("missing_operation_evidence")
    if evidence.context != operation.context:
        raise TikTokAuthorizationError("binding_mismatch")
    if evidence.operation_id != operation.id:
        raise TikTokAuthorizationError("operation_mismatch")
    if evidence.grant_id != grant.id or evidence.grant_revision != grant.revision:
        raise TikTokAuthorizationError("stale_grant")
    if not operation.started_at <= evidence.verified_at <= now:
        raise TikTokAuthorizationError("invalid_evidence_time")
