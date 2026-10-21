"""Pure workspace shapes and policy checks, not authentication or dispatch."""

from __future__ import annotations

import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

PageKey = Literal["workspace_home", "business_orders", "business_order"]
WorkspaceRole = Literal["platform_admin", "tenant_admin", "tenant_staff", "read_only"]
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=100)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=64)]
MAX_WORK_TTL = timedelta(minutes=15)
MAX_EVIDENCE_AGE = timedelta(seconds=30)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class OrderHint(_Contract):
    kind: Literal["local_order"]
    order_id: BoundedText

    @field_validator("order_id")
    @classmethod
    def valid_identifier(cls, value: str) -> str:
        if not value.strip() or any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("order identifier must be nonblank and contain no control characters")
        return value


class OverviewPageHint(_Contract):
    page: Literal["workspace_home", "business_orders"]


class OrderPageHint(_Contract):
    page: Literal["business_order"]
    entity: OrderHint


PageHint = Annotated[OverviewPageHint | OrderPageHint, Field(discriminator="page")]


class WorkspaceTurnInput(_Contract):
    request_key: UUID
    body: str = Field(min_length=1, max_length=4000)
    page_hint: PageHint | None = None

    @field_validator("body")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class GuidanceProposal(_Contract):
    action: Literal["page_guidance"]
    page: PageKey


class OrderReadProposal(_Contract):
    action: Literal["local_order_read"]
    resource: OrderHint


WorkspaceActionProposal = Annotated[
    GuidanceProposal | OrderReadProposal, Field(discriminator="action")
]


class VerifiedWorkspacePrincipal(_Contract):
    """Trusted adapter snapshot only; construction does not verify a session."""

    user_id: UUID
    session_id: UUID
    tenant_id: UUID
    role: WorkspaceRole
    verified_at: AwareDatetime


class WorkspaceWorkBinding(_Contract):
    """Persistable shape only; grants and credentials deliberately absent."""

    work_id: UUID
    thread_id: UUID
    owner_user_id: UUID
    session_id: UUID
    tenant_id: UUID
    request_key: UUID
    created_at: AwareDatetime
    expires_at: AwareDatetime
    proposal: WorkspaceActionProposal
    expected_resource_updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_binding(self) -> WorkspaceWorkBinding:
        ttl = self.expires_at - self.created_at
        if not timedelta() < ttl <= MAX_WORK_TTL:
            raise ValueError("work lifetime must be positive and at most fifteen minutes")
        if isinstance(self.proposal, OrderReadProposal):
            if self.expected_resource_updated_at is None:
                raise ValueError("order work requires the original resource version")
            if self.expected_resource_updated_at > self.created_at:
                raise ValueError("original resource version cannot be in the future")
        elif self.expected_resource_updated_at is not None:
            raise ValueError("guidance has no resource version")
        return self


class ResolvedWorkspaceAuthority(_Contract):
    """Fresh trusted resolver output, never a browser/model request contract."""

    work_id: UUID
    thread_id: UUID
    tenant_id: UUID
    user_id: UUID
    session_id: UUID
    proposal: WorkspaceActionProposal
    checked_at: AwareDatetime
    capability_enabled: bool
    action_allowed: bool
    resource_allowed: bool
    resource_updated_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def valid_resource_version(self) -> ResolvedWorkspaceAuthority:
        if isinstance(self.proposal, OrderReadProposal):
            if self.resource_allowed and self.resource_updated_at is None:
                raise ValueError("authorized order requires a current resource version")
            if self.resource_updated_at is not None and self.resource_updated_at > self.checked_at:
                raise ValueError("resource version cannot be in the future")
        elif self.resource_updated_at is not None:
            raise ValueError("guidance has no resource version")
        return self


DenialCode = Literal[
    "principal_unavailable",
    "authority_unavailable",
    "binding_mismatch",
    "action_forbidden",
    "capability_unavailable",
    "resource_unavailable",
    "stale_resource",
    "proposal_expired",
]


class WorkspaceAllowed(_Contract):
    status: Literal["allowed"] = "allowed"
    risk: Literal["read"] = "read"


class WorkspaceDeniedResult(_Contract):
    status: Literal["denied"] = "denied"
    code: DenialCode


WorkspacePolicyDecision = Annotated[
    WorkspaceAllowed | WorkspaceDeniedResult, Field(discriminator="status")
]


def validate_workspace_execution(
    binding: WorkspaceWorkBinding,
    principal: VerifiedWorkspacePrincipal | None,
    authority: ResolvedWorkspaceAuthority | None,
    *,
    now: datetime,
) -> WorkspaceAllowed | WorkspaceDeniedResult:
    """Compare synthetic/resolved evidence; no I/O, authentication or race fence."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("policy clock must be timezone-aware")
    now = now.astimezone(UTC)
    if principal is None:
        return WorkspaceDeniedResult(code="principal_unavailable")
    if (
        principal.user_id != binding.owner_user_id
        or principal.session_id != binding.session_id
        or principal.tenant_id != binding.tenant_id
    ):
        return WorkspaceDeniedResult(code="binding_mismatch")
    if principal.role == "read_only":
        return WorkspaceDeniedResult(code="action_forbidden")
    if now < binding.created_at:
        return WorkspaceDeniedResult(code="binding_mismatch")
    if now >= binding.expires_at:
        return WorkspaceDeniedResult(code="proposal_expired")
    if not binding.created_at <= principal.verified_at <= now:
        return WorkspaceDeniedResult(code="principal_unavailable")
    if now - principal.verified_at > MAX_EVIDENCE_AGE:
        return WorkspaceDeniedResult(code="principal_unavailable")
    if authority is None:
        return WorkspaceDeniedResult(code="authority_unavailable")
    if (
        authority.work_id != binding.work_id
        or authority.thread_id != binding.thread_id
        or authority.user_id != binding.owner_user_id
        or authority.session_id != binding.session_id
        or authority.tenant_id != binding.tenant_id
        or authority.proposal != binding.proposal
    ):
        return WorkspaceDeniedResult(code="binding_mismatch")
    if (
        not binding.created_at <= authority.checked_at <= now
        or now - authority.checked_at > MAX_EVIDENCE_AGE
    ):
        return WorkspaceDeniedResult(code="authority_unavailable")
    if not authority.capability_enabled:
        return WorkspaceDeniedResult(code="capability_unavailable")
    if not authority.action_allowed:
        return WorkspaceDeniedResult(code="action_forbidden")
    if not authority.resource_allowed:
        return WorkspaceDeniedResult(code="resource_unavailable")
    if authority.resource_updated_at != binding.expected_resource_updated_at:
        return WorkspaceDeniedResult(code="stale_resource")
    return WorkspaceAllowed()


class GuidanceResult(_Contract):
    status: Literal["completed"] = "completed"
    action: Literal["page_guidance"] = "page_guidance"
    page: PageKey
    instruction_key: Literal["workspace_home_help", "business_orders_help", "business_order_help"]
    source: Literal["application_guide"] = "application_guide"
    as_of: AwareDatetime

    @model_validator(mode="after")
    def matching_instruction(self) -> GuidanceResult:
        if self.instruction_key != f"{self.page}_help":
            raise ValueError("instruction must match the guidance page")
        return self


class LocalOrderSummary(_Contract):
    """Bounded manual-business projection; no contact, address or provider blob."""

    resource: OrderHint
    status: ShortText
    payment_status: ShortText
    total: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    channel: ShortText
    external_order_id: BoundedText | None
    external_shop_id: BoundedText | None
    tracking_no: BoundedText | None
    carrier: BoundedText | None
    placed_at: AwareDatetime | None
    shipped_at: AwareDatetime | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class OrderReadResult(_Contract):
    status: Literal["completed"] = "completed"
    action: Literal["local_order_read"] = "local_order_read"
    order: LocalOrderSummary
    source: Literal["local_records"] = "local_records"
    marketplace_sync: Literal["unavailable"] = "unavailable"
    as_of: AwareDatetime


class WorkspaceUnavailableResult(_Contract):
    status: Literal["unavailable"] = "unavailable"
    code: Literal["identity_unavailable", "policy_unavailable", "business_unavailable"]
    retryable: bool


CompletedResult = GuidanceResult | OrderReadResult
WorkspaceActionResult = CompletedResult | WorkspaceDeniedResult | WorkspaceUnavailableResult


class CreateWorkspaceThread(_Contract):
    request_key: UUID


class WorkspaceThreadOut(_Contract):
    id: UUID
    sequence: int
    created_at: AwareDatetime
    duplicate: bool = False


class WorkspaceThreadPage(_Contract):
    items: list[WorkspaceThreadOut]
    next_cursor: int | None


class WorkspaceTurnOut(_Contract):
    id: int
    request_key: UUID
    body: str
    result: WorkspaceActionResult
    created_at: AwareDatetime
    duplicate: bool = False


class WorkspaceTurnPage(_Contract):
    items: list[WorkspaceTurnOut]
    next_cursor: int | None
