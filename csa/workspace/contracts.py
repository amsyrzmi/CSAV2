"""Pure workspace shapes and policy checks, not authentication or dispatch."""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal, Union
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

from csa.hr.people_types import EmploymentType, LeavingReason

Language = Literal["en", "ms"]
PageKey = Literal["workspace_home", "business_orders", "business_order"]
WorkspaceRole = Literal["platform_admin", "tenant_admin", "tenant_staff", "read_only"]
BoundedText = Annotated[str, StringConstraints(min_length=1, max_length=100)]
ShortText = Annotated[str, StringConstraints(min_length=1, max_length=64)]
PersonText = Annotated[str, StringConstraints(min_length=1, max_length=120)]
ClockText = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
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


TabKey = Literal[
    "overview",
    "services",
    "inbox",
    "knowledge_base",
    "business",
    "hr",
    "email",
    "subscriptions",
    "bookings",
    "training_library",
    "activity",
    "chat",
]


class RecordRef(_Contract):
    """An open record; a ``product`` is one TikTok Shop listing of ours (its local UUID)."""

    kind: Literal[
        "conversation", "approval", "training_request", "draft", "order", "employee", "product"
    ]
    id: str = Field(min_length=1, max_length=100)
    version: int | None = None


class PageContext(_Contract):
    tab: TabKey
    section: str | None = Field(default=None, max_length=40)
    record: RecordRef | None = None


class WorkspaceTurnInput(_Contract):
    request_key: UUID
    body: str = Field(min_length=1, max_length=4000)
    page_context: PageContext | None = None
    page_hint: PageHint | None = None  # legacy page-guidance path

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


class BookingFreeAsk(_Contract):
    """ "When is Aina free tomorrow?": whose free times and which local day, for the read
    ``booking_availability``. The server finds the person or room and answers from the booking
    rules; a busy time is never explained."""

    person: PersonText | None = None
    room: PersonText | None = None
    day: date
    minutes: int | None = Field(default=None, ge=1, le=1440)

    @model_validator(mode="after")
    def someone_or_somewhere(self) -> BookingFreeAsk:
        if (self.person is None) == (self.room is None):
            raise ValueError("set exactly one of person and room")
        return self


class AnswerProposal(_Contract):
    action: Literal["answer"]
    markdown: str = Field(min_length=1, max_length=8000)
    reads: list[
        Literal["read_current_record", "my_profile", "my_packages", "booking_availability"]
    ] = Field(default_factory=list, max_length=4)
    # Who and when for the read "booking_availability"; set with it and only with it.
    availability: BookingFreeAsk | None = None

    @model_validator(mode="after")
    def availability_goes_with_its_read(self) -> AnswerProposal:
        if ("booking_availability" in self.reads) != (self.availability is not None):
            raise ValueError("set availability with the booking_availability read, and only then")
        return self


class NavigateProposal(_Contract):
    action: Literal["navigate"]
    tab: TabKey
    section: str | None = Field(default=None, max_length=40)
    record: RecordRef | None = None
    resume: bool = False


class DirectoryLookupProposal(_Contract):
    action: Literal["directory_lookup"]
    query: str = Field(min_length=1, max_length=120)


# A CC/BCC entry or new-email recipient: a directory employee id or a plain address. Addresses
# are checked against the user's own words before anything is drafted (see the turn service).
RecipientRef = Annotated[str, StringConstraints(min_length=3, max_length=254)]
MAX_COPIES = 20


class EmailComposeProposal(_Contract):
    action: Literal["email_compose"]
    recipient_employee_id: UUID | None = None
    recipient_address: str | None = Field(default=None, min_length=3, max_length=254)
    cc: list[RecipientRef] = Field(default_factory=list, max_length=MAX_COPIES)
    bcc: list[RecipientRef] = Field(default_factory=list, max_length=MAX_COPIES)
    subject: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=1000)
    language: Literal["auto", "en", "ms"] = "auto"
    # True only when the user clearly asked to send: the card is then a send card, not an export.
    send: bool = False

    @model_validator(mode="after")
    def one_recipient(self) -> EmailComposeProposal:
        if (self.recipient_employee_id is None) == (self.recipient_address is None):
            raise ValueError("set exactly one of recipient_employee_id and recipient_address")
        return self


class EmailReplyProposal(_Contract):
    """A reply in a synced thread; its To comes from the thread, never from the model."""

    action: Literal["email_reply"]
    thread_id: str = Field(min_length=1, max_length=256)
    instructions: str = Field(default="", max_length=1000)
    cc: list[RecipientRef] = Field(default_factory=list, max_length=MAX_COPIES)
    bcc: list[RecipientRef] = Field(default_factory=list, max_length=MAX_COPIES)
    language: Literal["auto", "en", "ms"] = "auto"
    send: bool = False


class EmailTeamProposal(_Contract):
    """One email to the caller's direct reports, all in To; the server picks the people."""

    action: Literal["email_team"]
    subject: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=1000)
    language: Literal["auto", "en", "ms"] = "auto"
    send: bool = False


class InboxSummaryProposal(_Contract):
    """Answers "what needs my attention" from synced mail, with no card."""

    action: Literal["inbox_summary"]


class TrainingSubmitProposal(_Contract):
    action: Literal["training_submit"]
    request_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    learning_objective: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def request_or_new(self) -> TrainingSubmitProposal:
        new = self.title is not None and self.learning_objective is not None
        partial = (self.title is not None) != (self.learning_objective is not None)
        if partial or (self.request_id is not None) == new:
            raise ValueError("give request_id alone, or both title and learning_objective")
        return self


class InboxDecisionProposal(_Contract):
    action: Literal["inbox_decision"]
    approval_id: int = Field(ge=1)
    conversation_id: UUID
    decision: Literal["approve", "reject"]
    expected_version: int = Field(ge=0)


class ClarificationOption(_Contract):
    label: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=200)


class ClarificationProposal(_Contract):
    action: Literal["clarification"]
    question: str = Field(min_length=1, max_length=500)
    options: list[ClarificationOption] = Field(default_factory=list, max_length=5)


class ProductStockReadProposal(_Contract):
    """How many of a TikTok Shop product are in stock, read live (spec 6.8). Read-only.

    ``listing_id`` is the open product record or an option the user picked; ``query`` is the
    product words the user wrote. With neither, the open product record is read. There is no
    field for a count, a price or a status: the workspace assistant never changes TikTok Shop.
    """

    action: Literal["product_stock_read"]
    query: str | None = Field(default=None, min_length=1, max_length=100)
    listing_id: UUID | None = None


# People actions (csa/workspace/hr_people.py resolves every name against People as the caller).
# Names, titles and departments are the words the user wrote; the server finds the people.
class HrPersonAddProposal(_Contract):
    """Add a person; the server asks for a missing name, job title or start date."""

    action: Literal["hr_person_add"]
    display_name: PersonText | None = None
    job_title: BoundedText | None = None
    start_date: date | None = None
    department: PersonText | None = None
    manager: PersonText | None = None
    employment_type: EmploymentType | None = None
    work_email: PersonText | None = None


class HrPersonUpdateProposal(_Contract):
    """Change a person; ``person=None`` means the open employee record. Unset fields stay."""

    action: Literal["hr_person_update"]
    person: PersonText | None = None
    job_title: BoundedText | None = None
    department: PersonText | None = None
    manager: PersonText | None = None
    work_phone: PersonText | None = None
    employment_type: EmploymentType | None = None
    start_date: date | None = None


class HrPersonLeaveProposal(_Contract):
    """Mark a person as leaving; the server asks for a missing last day or reason."""

    action: Literal["hr_person_leave"]
    person: PersonText | None = None
    last_day: date | None = None
    reason: LeavingReason | None = None
    note: PersonText | None = None


class PeopleQueryProposal(_Contract):
    """Who reports to someone, or who is in a department, within the caller's People access."""

    action: Literal["people_query"]
    reports_to: PersonText | None = None
    department: PersonText | None = None

    @model_validator(mode="after")
    def something_to_ask(self) -> PeopleQueryProposal:
        if self.reports_to is None and self.department is None:
            raise ValueError("set reports_to or department")
        return self


class HrPersonOpenProposal(_Contract):
    """Open a person's profile page."""

    action: Literal["hr_person_open"]
    person: PersonText


class BookingActionProposal(_Contract):
    """Book, move or cancel; every name is the user's own words and the server finds the rest.

    ``people``, ``rooms``, ``booking_type``, ``customer`` and ``booking_id`` may also hold an id
    the user picked from offered options. To book give a ``booking_type`` (or a ``title`` and
    ``minutes`` for a meeting), the local ``day`` and ``time``, and who or which room. To move or
    cancel say which booking (``booking_id`` from an option, or ``on_day``, ``at_time`` and
    ``about``); a move also gives the new ``time`` (and ``day`` when it changes). Nothing missing
    is guessed: the server asks.
    """

    action: Literal["booking_action"]
    change: Literal["book", "move", "cancel"]
    booking_type: PersonText | None = None
    title: BoundedText | None = None
    minutes: int | None = Field(default=None, ge=1, le=1440)
    people: list[PersonText] = Field(default_factory=list, max_length=10)
    rooms: list[PersonText] = Field(default_factory=list, max_length=10)
    include_me: bool = False
    customer: PersonText | None = None
    day: date | None = None
    time: ClockText | None = None
    booking_id: PersonText | None = None
    on_day: date | None = None
    at_time: ClockText | None = None
    about: PersonText | None = None


class RecipientSpec(_Contract):
    """One recipient of a batch email, as the user named it; the server finds the person.

    ``employee`` and ``customer`` hold a name, ``address`` a typed address, and ``group`` a fixed
    group (``group`` set; ``renewing_soon`` also needs ``days``) with ``value`` left empty.
    """

    kind: Literal["employee", "address", "customer", "group"]
    value: str = Field(default="", max_length=254)
    group: Literal["active_subscribers", "renewing_soon"] | None = None
    days: int | None = Field(default=None, ge=1, le=365)

    @model_validator(mode="after")
    def complete(self) -> RecipientSpec:
        if self.kind == "group":
            if self.group is None:
                raise ValueError("a group recipient needs a group")
            if self.group == "renewing_soon" and self.days is None:
                raise ValueError("renewing_soon needs days")
        elif not self.value.strip():
            raise ValueError("a recipient needs a value")
        return self


class EmailBatchProposal(_Contract):
    """One email to several people; the server expands it into one draft step per recipient.

    ``send`` is only the planner's reading of the user's wish: the send rule decides.
    """

    action: Literal["email_batch"]
    recipients: list[RecipientSpec] = Field(min_length=1, max_length=40)
    subject: str = Field(min_length=1, max_length=200)
    instructions: str = Field(default="", max_length=1000)
    language: Literal["auto", "en", "ms"] = "auto"
    send: bool = False
    offset: int = Field(default=0, ge=0)


PlannerStep = Annotated[
    Union[  # noqa: UP007 - Annotated discriminated union of named members
        BookingActionProposal,
        TrainingSubmitProposal,
        InboxDecisionProposal,
        EmailBatchProposal,
    ],
    Field(discriminator="action"),
]


class PlanProposal(_Contract):
    """Several actions behind one card and one confirm; the steps are what the planner chose."""

    action: Literal["plan"]
    steps: list[PlannerStep] = Field(min_length=1, max_length=20)


PlanStepKind = Literal[
    "email_send",
    "email_export",
    "booking_action",
    "inbox_decision",
    "training_submit",
    "hr_person",
]


class PlanStep(_Contract):
    """One stored step of a plan card: the child proposal's kind and payload, never a plan."""

    kind: PlanStepKind
    payload: dict[str, object]
    version: str | None = None


class PlanProposalPayload(_Contract):
    """The whole stored payload of a plan card."""

    steps: list[PlanStep] = Field(min_length=1, max_length=20)


WorkspaceProposal = Annotated[
    Union[  # noqa: UP007 - Annotated discriminated union of named members
        AnswerProposal,
        NavigateProposal,
        DirectoryLookupProposal,
        EmailComposeProposal,
        EmailReplyProposal,
        EmailTeamProposal,
        InboxSummaryProposal,
        TrainingSubmitProposal,
        InboxDecisionProposal,
        ClarificationProposal,
        HrPersonAddProposal,
        HrPersonUpdateProposal,
        HrPersonLeaveProposal,
        PeopleQueryProposal,
        HrPersonOpenProposal,
        BookingActionProposal,
        EmailBatchProposal,
        PlanProposal,
        GuidanceProposal,
        OrderReadProposal,
        ProductStockReadProposal,
    ],
    Field(discriminator="action"),
]
WorkspaceActionProposal = WorkspaceProposal

ProposalKind = Literal[
    "email_export",
    "email_send",
    "training_submit",
    "inbox_decision",
    "subscription_action",
    "hr_person",
    "booking_action",
    "plan",
]


class EmailExportProposal(_Contract):
    action: Literal["email_export"]
    draft_id: UUID


class EmailSendProposal(_Contract):
    """The whole stored payload of a send card: no address, subject or body.

    ``version`` is ``csa.email.models.draft_version`` of the draft when the card was made, so
    any edit afterwards makes the card stale.
    """

    action: Literal["email_send"]
    draft_id: UUID
    version: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProposalCreate(_Contract):
    """Direct proposal creation from a tab; ``payload`` is validated by the kind's executor."""

    kind: ProposalKind
    payload: dict[str, object]
    thread_id: UUID | None = None


class CardField(_Contract):
    label: str
    value: str


class ProposalCard(_Contract):
    proposal_id: UUID
    kind: ProposalKind
    title: str
    summary: str
    fields: list[CardField]
    expires_at: AwareDatetime
    expected_version: str | None
    language: Language = "en"


class ProposalOut(_Contract):
    id: UUID
    kind: ProposalKind
    status: Literal["requested", "confirmed", "executed", "expired", "cancelled"]
    card: ProposalCard
    result: dict[str, object] | None
    created_at: AwareDatetime
    expires_at: AwareDatetime


class PersonOut(_Contract):
    employee_id: UUID
    display_name: str
    job_title: str | None
    work_email: str | None


TeamReason = Literal[
    "package_inactive",
    "not_team_approver",
    "no_employee_link",
    "no_reports",
    "no_work_email",
    "team_too_large",
]
"""Why the caller's team cannot be emailed: no HR, no approves-team switch, no employee link,
nobody reporting, no report with a work email, or more than 20 reports to email."""


class MyPeopleOut(_Contract):
    employee_id: UUID | None
    manager: PersonOut | None
    reason: Literal["no_employee_link", "no_manager", "package_inactive"] | None


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
    # Whether the tenant has a connected TikTok Shop now (csa/console/business.py
    # marketplace_sync_state). "unavailable" only replays turns stored before 4 Nov 2026; the
    # turn service never writes it.
    marketplace_sync: Literal["connected", "not_connected", "unavailable"]
    as_of: AwareDatetime


class StockVariant(_Contract):
    """One variant's count; ``stock`` is None when TikTok gave no count."""

    variant: str = Field(max_length=255)
    seller_sku: str | None = Field(default=None, max_length=100)
    stock: int | None


class ProductStockResult(_Contract):
    """A TikTok Shop product's stock per variant, live or from our copy; no ids.

    ``note`` is the server's line in the turn's language: "checked live just now", or how old
    our copy is when the live read failed. Every SKU of the product is listed with its count,
    on sale or not; ``on_sale`` is false when TikTok does not list the product as selling.
    """

    action: Literal["product_stock_read"]
    source: Literal["tiktok_shop"] = "tiktok_shop"
    product: str = Field(min_length=1, max_length=255)
    variants: list[StockVariant] = Field(max_length=300)
    total: int = Field(ge=0)
    checked_live: bool
    on_sale: bool
    as_of: AwareDatetime
    note: str = Field(min_length=1, max_length=200)


class WorkspaceUnavailableResult(_Contract):
    status: Literal["unavailable"] = "unavailable"
    code: Literal["identity_unavailable", "policy_unavailable", "business_unavailable"]
    retryable: bool


CompletedResult = GuidanceResult | OrderReadResult


class ProposalResult(_Contract):
    action: Literal["proposal"]
    proposal: ProposalOut


class AnswerResult(_Contract):
    action: Literal["answer"]
    markdown: str


class NavigateResult(_Contract):
    action: Literal["navigate"]
    tab: TabKey
    section: str | None = None
    record: RecordRef | None = None
    resume: bool = False
    # A console page to open instead of the tab's own (a person's profile); server-built only.
    path: str | None = None


class DirectoryResult(_Contract):
    action: Literal["directory_lookup"]
    candidates: list[PersonOut]


class ClarificationResult(_Contract):
    action: Literal["clarification"]
    question: str
    options: list[ClarificationOption]


WorkspaceActionResult = (
    CompletedResult
    | WorkspaceDeniedResult
    | WorkspaceUnavailableResult
    | ProposalResult
    | AnswerResult
    | NavigateResult
    | DirectoryResult
    | ClarificationResult
    | ProductStockResult
)


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
    language: Language = "en"


class WorkspaceTurnPage(_Contract):
    items: list[WorkspaceTurnOut]
    next_cursor: int | None
