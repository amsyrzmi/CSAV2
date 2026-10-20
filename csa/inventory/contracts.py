"""Offline count-command contracts, not authentication or persistence adapters."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from typing import Annotated, Literal, Protocol
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

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
Quantity = Annotated[int, Field(strict=True, ge=0, le=2_147_483_647)]
Version = Annotated[int, Field(strict=True, ge=0, le=9_223_372_036_854_775_807)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class InventoryAccountKey(_Contract):
    client_id: UUID
    product_id: UUID
    warehouse_id: UUID


class TenantAdminEvidence(_Contract):
    """A server-loaded identity snapshot; constructing it proves no authority."""

    client_id: UUID
    user_id: UUID
    session_id: UUID
    role: Literal["tenant_admin"]
    verified_at: AwareDatetime

    @field_validator("verified_at")
    @classmethod
    def normalize_time(cls, value: AwareDatetime) -> AwareDatetime:
        return value.astimezone(UTC)


class StockChangeCommand(_Contract):
    schema_version: Literal[1] = 1
    command_id: UUID
    account: InventoryAccountKey
    submitter: TenantAdminEvidence
    kind: Literal["physical_count", "manual_adjustment"]
    reason: Reason
    expected_version: Version
    expected_authority_revision: Annotated[int, Field(strict=True, ge=1)]
    before_on_hand: Quantity
    value: Annotated[int, Field(strict=True, ge=-2_147_483_647, le=2_147_483_647)]

    @model_validator(mode="after")
    def validate_scope_and_quantity(self) -> StockChangeCommand:
        if self.submitter.client_id != self.account.client_id:
            raise ValueError("submitter tenant does not match account")
        if self.kind == "physical_count" and self.value < 0:
            raise ValueError("physical count cannot be negative")
        if not 0 <= self.proposed_on_hand <= 2_147_483_647:
            raise ValueError("resulting on-hand is outside quantity range")
        return self

    @property
    def proposed_on_hand(self) -> int:
        return self.value if self.kind == "physical_count" else self.before_on_hand + self.value

    @property
    def requires_second_admin(self) -> bool:
        return self.proposed_on_hand != self.before_on_hand

    def fingerprint(self) -> str:
        # Session renewal is not a new business payload; retain its evidence separately.
        payload = self.model_dump(mode="json", exclude={"submitter"})
        payload["submitter_user_id"] = str(self.submitter.user_id)
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        )
        return hashlib.sha256(canonical.encode("ascii")).hexdigest()


class StockChangeDecision(_Contract):
    command: StockChangeCommand
    reviewer: TenantAdminEvidence
    outcome: Literal["approved", "rejected"]
    reason: Reason

    @model_validator(mode="after")
    def validate_reviewer(self) -> StockChangeDecision:
        if self.reviewer.client_id != self.command.account.client_id:
            raise ValueError("reviewer tenant does not match account")
        if self.reviewer.user_id == self.command.submitter.user_id:
            raise ValueError("submitter cannot review their own command")
        if not self.command.requires_second_admin:
            raise ValueError("zero-variance command is recorded without review")
        return self


def require_exact_stock_change_replay(
    stored: StockChangeCommand, candidate: StockChangeCommand
) -> None:
    if stored.fingerprint() != candidate.fingerprint():
        raise ValueError("stock-change command replay differs")


class StockChangeRecord(_Contract):
    """Persistable shape only; no database writer or transition authority."""

    command: StockChangeCommand
    state: Literal["pending", "recorded", "approved", "rejected"]
    decision: StockChangeDecision | None = None
    resulting_version: Version | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> StockChangeRecord:
        if self.state in {"pending", "recorded"}:
            expected = "pending" if self.command.requires_second_admin else "recorded"
            if self.state != expected or self.decision is not None:
                raise ValueError("command outcome does not match review requirement")
            version = self.command.expected_version if self.state == "recorded" else None
        else:
            if self.decision is None or self.decision.outcome != self.state:
                raise ValueError("terminal outcome requires matching decision")
            # Retain the original evidence, not merely an equivalent business fingerprint.
            if self.decision.command != self.command:
                raise ValueError("decision does not reference the retained command")
            version = self.command.expected_version + 1 if self.state == "approved" else None
        if self.resulting_version != version:
            raise ValueError("outcome has an invalid resulting version")
        return self

    @property
    def freezes_new_reservations(self) -> bool:
        return self.state == "pending"

    def replay(self, candidate: StockChangeCommand) -> StockChangeRecord:
        require_exact_stock_change_replay(self.command, candidate)
        return self


class StockChangeAuditEvent(_Contract):
    event_id: UUID
    command: StockChangeCommand
    kind: Literal["pending", "recorded", "approved", "rejected"]
    actor: TenantAdminEvidence
    occurred_at: AwareDatetime
    fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    observed_version: Version
    observed_authority_revision: Annotated[int, Field(strict=True, ge=1)]
    before_on_hand: Quantity
    after_on_hand: Quantity
    resulting_version: Version
    decision: StockChangeDecision | None = None

    @model_validator(mode="after")
    def validate_audit_transition(self) -> StockChangeAuditEvent:
        if self.actor.client_id != self.command.account.client_id:
            raise ValueError("event actor tenant does not match command")
        if self.occurred_at < self.actor.verified_at:
            raise ValueError("event occurrence predates actor verification")
        if self.fingerprint != self.command.fingerprint():
            raise ValueError("event fingerprint does not match command")

        if self.kind in {"pending", "recorded"}:
            if self.decision is not None:
                raise ValueError("request event cannot contain a decision")
            if self.before_on_hand != self.command.before_on_hand:
                raise ValueError("request event before quantity differs from command")
            if (
                self.observed_version != self.command.expected_version
                or self.observed_authority_revision != self.command.expected_authority_revision
                or self.after_on_hand != self.before_on_hand
                or self.resulting_version != self.observed_version
            ):
                raise ValueError("request event does not match observed command state")
            expected_kind = "pending" if self.command.requires_second_admin else "recorded"
            if self.kind != expected_kind:
                raise ValueError("request event kind does not match variance")
        else:
            if self.decision is None or self.decision.command != self.command:
                raise ValueError("terminal event requires matching decision")
            if self.decision.outcome != self.kind or self.decision.reviewer != self.actor:
                raise ValueError("terminal event actor or decision differs")
            if self.before_on_hand != self.command.before_on_hand and self.kind == "approved":
                raise ValueError("approved event before quantity differs from command")
            if self.kind == "approved":
                if (
                    self.observed_version != self.command.expected_version
                    or self.observed_authority_revision != self.command.expected_authority_revision
                    or self.before_on_hand != self.command.before_on_hand
                    or self.after_on_hand != self.command.proposed_on_hand
                    or self.resulting_version != self.observed_version + 1
                ):
                    raise ValueError("approval event does not match command fences")
            elif (
                self.after_on_hand != self.before_on_hand
                or self.resulting_version != self.observed_version
            ):
                raise ValueError("rejection event cannot mutate stock")
        return self


class TenantAdminLoader(Protocol):
    """Future adapter must revalidate session and membership inside the transaction."""

    async def __call__(
        self, *, client_id: UUID, user_id: UUID, session_id: UUID
    ) -> TenantAdminEvidence: ...


async def reload_tenant_admin(
    claimed: TenantAdminEvidence, *, loader: TenantAdminLoader
) -> TenantAdminEvidence:
    current = await loader(
        client_id=claimed.client_id, user_id=claimed.user_id, session_id=claimed.session_id
    )
    if (
        current.client_id != claimed.client_id
        or current.user_id != claimed.user_id
        or current.session_id != claimed.session_id
        or current.verified_at < claimed.verified_at
    ):
        raise ValueError("reloaded admin evidence does not match requested principal")
    return current
