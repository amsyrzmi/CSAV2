"""Contracts for the staff email assistant (persisted, HTTP and model-output shapes)."""

from __future__ import annotations

import hashlib
import json
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

from csa.agents.staged.models import EmailDraftInput, validate_mailbox_address

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
DraftStatus = Literal["draft", "exported", "sending", "sent"]
MAX_ADDRESSES = 20
MAX_ADDRESS_CHARS = 254
SEND_OUTCOME_UNKNOWN_MESSAGE = "Check your Gmail Sent folder before trying again."


def check_address(value: str) -> str:
    """One plain mailbox address under the draft contract's rules, at most 254 characters."""
    if len(value) > MAX_ADDRESS_CHARS:
        raise ValueError("address too long")
    return validate_mailbox_address(value)


def is_employee_ref(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def _recipient_refs(values: list[str]) -> list[str]:
    """Each entry is a directory employee id or a plain address."""
    return [
        str(UUID(value)) if is_employee_ref(value) else check_address(value) for value in values
    ]


class EmailModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EmailPolicyError(ValueError):
    """Stable, content-free domain error."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class SendLimitReached(EmailPolicyError):
    """The hourly send limit; `retry_after` is the seconds until the oldest send leaves it."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("send_limit_reached")
        self.retry_after = retry_after


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
    """A new email: one To recipient (a directory employee or a plain address), optional CC/BCC.

    CC and BCC entries are directory employee ids or plain addresses.
    """

    recipient_employee_id: UUID | None = None
    recipient_address: str | None = Field(default=None, min_length=3, max_length=254)
    cc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    bcc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    subject: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=1000)
    language: Literal["auto", "en", "ms"] = "auto"

    @field_validator("recipient_address")
    @classmethod
    def plain_address(cls, value: str | None) -> str | None:
        return None if value is None else EmailDraftInput.validate_recipient(value)

    @field_validator("cc", "bcc")
    @classmethod
    def refs(cls, values: list[str]) -> list[str]:
        return _recipient_refs(values)

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


class ReplyRequest(EmailModel):
    """A reply in a synced thread. The To address comes from the thread, never from here."""

    instructions: str = Field(default="", max_length=1000)
    cc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    bcc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    language: Literal["auto", "en", "ms"] = "auto"

    @field_validator("cc", "bcc")
    @classmethod
    def refs(cls, values: list[str]) -> list[str]:
        return _recipient_refs(values)


class RecipientOut(EmailModel):
    address: str
    outside: bool


class DraftOut(EmailModel):
    id: UUID
    thread_id: str | None
    recipient: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    recipients: list[RecipientOut]
    subject: str
    body: str
    status: DraftStatus
    source: Literal["model", "human"]
    gmail_draft_id: str | None
    sent_at: AwareDatetime | None
    needs_staff_input: bool
    reply_to_message_id: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime


class DraftUpdate(EmailModel):
    """An edit by the person. `recipient` alone is the older single-address form of `to`."""

    recipient: str | None = Field(default=None, min_length=3, max_length=254)
    to: list[str] | None = Field(default=None, max_length=MAX_ADDRESSES)
    cc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    bcc: list[str] = Field(default_factory=list, max_length=MAX_ADDRESSES)
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=20000)

    @field_validator("recipient")
    @classmethod
    def plain_recipient(cls, value: str | None) -> str | None:
        return None if value is None else check_address(value)

    @field_validator("to", "cc", "bcc")
    @classmethod
    def plain_addresses(cls, values: list[str] | None) -> list[str] | None:
        return None if values is None else [check_address(value) for value in values]

    @model_validator(mode="after")
    def some_recipient(self) -> DraftUpdate:
        if self.to is None:
            self.to = [self.recipient] if self.recipient is not None else []
        if not self.to and not self.cc and not self.bcc:
            raise ValueError("at least one recipient required")
        return self


class SendRequest(EmailModel):
    version: str = Field(pattern=r"^[0-9a-f]{64}$")


class SendResult(EmailModel):
    """The outcome of one send. An unknown outcome is refused as `send_outcome_unknown`."""

    outcome: Literal["sent", "unknown"]
    sent_at: AwareDatetime | None
    gmail_message_id: str | None
    remaining_this_hour: int = Field(ge=0)


def draft_version(draft: DraftOut) -> str:
    """Hash of exactly what a send card shows; any edit to these fields changes it."""
    content = [draft.thread_id, draft.to, draft.cc, draft.bcc, draft.subject, draft.body]
    return hashlib.sha256(json.dumps(content, ensure_ascii=True).encode("ascii")).hexdigest()


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
