"""What the Google Calendar routes say. No answer ever holds a token."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from csa.console.contracts import Contract

ConnectionState = Literal["not_connected", "connected", "needs_reconnect"]


class CalendarPersonOut(Contract):
    """One bookable person with an account, and where their connection stands (Admins only)."""

    person_id: UUID
    name: str
    status: ConnectionState


class CalendarStatusOut(Contract):
    """`available` is false when the feature is off or the Google client is not set up.
    `can_connect` is true for a member linked to an active bookable person who can change
    bookings, and `status` is theirs. `people` is for an Admin, as a status only.
    """

    available: bool
    can_connect: bool
    status: ConnectionState | None
    people: list[CalendarPersonOut] | None


class CalendarConnectOut(Contract):
    url: str
