"""Pydantic contracts for the staff routes that link contacts and settle link reviews."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime

from csa.console.contracts import Contract

LinkReason = Literal["verified_phone", "staff"]
ReviewReason = Literal[
    "several_matches",
    "telegram_conflict",
    "same_channel_identity",
    "table_conflict",
    "previously_unlinked",
]


class LinkedContact(Contract):
    """A contact as the link screens show it: no email, no Telegram account, no prior values."""

    id: UUID
    full_name: str | None
    phone: str | None
    # The channels the contact has an identity on now. For the loser of a link that is not yet
    # undone, also the channels its identities had before the link moved them to the survivor.
    channels: list[str]


class LinkIn(Contract):
    contact_a: UUID
    contact_b: UUID


class LinkOut(Contract):
    id: UUID
    survivor: LinkedContact
    loser: LinkedContact
    reason: LinkReason
    created_at: AwareDatetime
    unlinked_at: AwareDatetime | None
    # Not undone, within 30 days, and no later active link on either contact.
    can_unlink: bool


class UnlinkResultOut(Contract):
    # Contact columns the unlink left alone, as "role.column" (e.g. "survivor.telegram_id").
    skipped: list[str]


class ReviewOut(Contract):
    id: UUID
    contact_a: LinkedContact
    contact_b: LinkedContact
    reason: ReviewReason
    status: Literal["open", "resolved"]
    resolution: Literal["linked", "dismissed"] | None
    created_at: AwareDatetime


class ResolveIn(Contract):
    resolution: Literal["dismissed", "linked"]
