"""Contracts for the staff email assistant (persisted, HTTP and model-output shapes)."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from csa.agents.staged.models import EmailDraftInput

MODEL_DISCLOSURE = (
    "Analyse and Draft send the selected email text to the configured model provider. "
    "Nothing is sent to anyone; drafts are saved locally or to your own Gmail drafts."
)
Category = Literal[
    "customer_request",
    "order_or_delivery",
    "complaint",
    "billing",
    "vendor_or_partner",
    "internal",
    "newsletter_or_promotion",
    "spam_or_phishing",
    "other",
]
Priority = Literal["low", "normal", "high"]
Role = Literal["tenant_admin", "tenant_staff", "read_only"]


class EmailModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmailPolicyError(ValueError):
    """Stable, content-free domain error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class EmailSession(EmailModel):
    """Trusted adapter input; the service revalidates inside its transaction."""

    session_id: UUID
    subject_id: UUID
    tenant_id: UUID
    role: Role | None
    token_hash: SecretStr = Field(exclude=True, repr=False)
    csrf_hash: SecretStr | None = Field(default=None, exclude=True, repr=False)


class MailboxOut(EmailModel):
    id: UUID
    address: str | None
    status: Literal["pending", "connected", "revoked"]
    scopes: list[str]
    last_sync_at: AwareDatetime | None


class EmailContext(EmailModel):
    enabled: bool
    can_manage: bool
    can_connect: bool
    provider_configured: bool
    model_configured: bool
    mailbox: MailboxOut | None
    disclosure: str = MODEL_DISCLOSURE


class SettingsIn(EmailModel):
    enabled: bool


class ConnectOut(EmailModel):
    authorization_url: str = Field(max_length=2048)


class Receipt(EmailModel):
    operation: str
    resource_id: str
    status: str
    duplicate: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)


class ThreadOut(EmailModel):
    thread_id: str
    subject: str
    sender: str
    snippet: str
    received_at: AwareDatetime | None
    labels: list[str]
    unread: bool
    category: Category | None
    priority: Priority | None
    summary: str | None
    action_items: list[str]
    analysed_at: AwareDatetime | None
    has_body: bool


class ThreadDetail(ThreadOut):
    body: str | None


class ThreadPage(EmailModel):
    items: list[ThreadOut]
    last_sync_at: AwareDatetime | None


class ThreadAnalysis(EmailModel):
    """Strict model output for one thread. Free text is bounded; no tool calls exist."""

    category: Category
    priority: Priority
    language: Literal["en", "ms", "mixed", "other"]
    summary: str = Field(min_length=1, max_length=600)
    action_items: list[str] = Field(default_factory=list, max_length=5)
    requires_human_decision: bool = False

    @field_validator("action_items")
    @classmethod
    def bounded_items(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if any(len(item) > 200 for item in cleaned):
            raise ValueError("action item too long")
        return cleaned


class DraftRequest(EmailModel):
    instructions: str = Field(default="", max_length=1000)
    language: Literal["auto", "en", "ms"] = "auto"


class ComposeRequest(EmailModel):
    """A new internal email: exactly one recipient, a directory employee or a plain address."""

    recipient_employee_id: UUID | None = None
    recipient_address: str | None = Field(default=None, min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=1000)
    language: Literal["auto", "en", "ms"] = "auto"

    @field_validator("recipient_address")
    @classmethod
    def plain_address(cls, value: str | None) -> str | None:
        return None if value is None else EmailDraftInput.validate_recipient(value)

    @model_validator(mode="after")
    def one_recipient(self) -> ComposeRequest:
        if (self.recipient_employee_id is None) == (self.recipient_address is None):
            raise ValueError("exactly one recipient required")
        return self


class DraftProposal(EmailModel):
    """Strict model output for a reply; validated again as EmailDraftInput before saving."""

    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)
    needs_facts_from_staff: list[str] = Field(default_factory=list, max_length=5)


class DraftOut(EmailModel):
    id: UUID
    thread_id: str | None
    recipient: str
    subject: str
    body: str
    status: Literal["draft", "exported"]
    source: Literal["model", "human"]
    gmail_draft_id: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DraftUpdate(EmailModel):
    recipient: str = Field(min_length=3, max_length=254)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)


class DraftPage(EmailModel):
    items: list[DraftOut]


def validated_draft(
    mailbox_id: UUID, thread_id: str, recipient: str, subject: str, body: str
) -> EmailDraftInput:
    """Reuse the staged email contract: header-injection and recipient rules."""
    return EmailDraftInput(
        mailbox_connection_id=mailbox_id,
        thread_id=thread_id,
        recipient=recipient,
        subject=subject,
        body=body,
    )
