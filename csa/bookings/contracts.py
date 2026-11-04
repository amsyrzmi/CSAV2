"""Pydantic contracts for the bookings setup API: people, rooms, types, hours, days and blocks."""

from __future__ import annotations

import unicodedata
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, Field, StringConstraints, model_validator

from csa.console.contracts import Contract

_LINE_SEPARATORS = (chr(0x2028), chr(0x2029))  # categories Zl and Zp


def _one_line(value: str) -> str:
    """Refuse line breaks and every other control or format character.

    Names, addresses and links end up in emails and calendar files, where a line break would
    start a new header or property.
    """
    if any(unicodedata.category(ch)[0] == "C" or ch in _LINE_SEPARATORS for ch in value):
        raise ValueError("must be a single line of plain text")
    return value


def _line(max_length: int) -> StringConstraints:
    return StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)


Name = Annotated[str, _line(120), AfterValidator(_one_line)]
Address = Annotated[str, _line(300), AfterValidator(_one_line)]
Note = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=200), AfterValidator(_one_line)
]
MeetingLink = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=500, pattern=r"^https://[^\s/]\S*$"),
    AfterValidator(_one_line),
]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^\s@<>,;\"]+@[^\s@<>,;\"]+\.[^\s@<>,;\"]+$",
    ),
    AfterValidator(_one_line),
]
ClockTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

Audience = Literal["customers", "staff", "both"]
Delivery = Literal["named", "any", "several"]
ResourceRule = Literal["none", "specific", "any"]
LocationKind = Literal["in_person", "phone", "online"]
SubjectKind = Literal["person", "resource"]
HoursKind = Literal["company", "person", "resource"]


class _Patch(Contract):
    """A partial change: the version read, then only the fields that are present."""

    version: int = Field(ge=1)

    def changes(self) -> dict[str, object]:
        """The fields the caller sent, including the ones sent as null."""
        return self.model_dump(exclude_unset=True, exclude={"version"})

    def refuse_null(self, *names: str) -> None:
        """A column that cannot be empty cannot be patched to null."""
        for name in names:
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")


class BookingPersonOut(Contract):
    id: UUID
    display_name: str
    user_id: UUID | None
    employee_id: UUID | None
    email: str | None
    is_active: bool
    version: int


class BookingPersonIn(Contract):
    display_name: Name
    user_id: UUID | None = None
    employee_id: UUID | None = None
    email: Email | None = None


class BookingPersonPatch(_Patch):
    display_name: Name | None = None
    user_id: UUID | None = None
    employee_id: UUID | None = None
    email: Email | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> BookingPersonPatch:
        self.refuse_null("display_name", "is_active")
        return self


class BookingResourceOut(Contract):
    id: UUID
    name: str
    capacity: int | None
    is_active: bool
    version: int


class BookingResourceIn(Contract):
    name: Name
    capacity: int | None = Field(default=None, ge=1, le=10000)


class BookingResourcePatch(_Patch):
    name: Name | None = None
    capacity: int | None = Field(default=None, ge=1, le=10000)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> BookingResourcePatch:
        self.refuse_null("name", "is_active")
        return self


class BookingTypeOut(Contract):
    id: UUID
    name: str
    duration_minutes: int
    buffer_minutes: int
    audience: Audience
    delivery: Delivery
    resource_rule: ResourceRule
    location_kind: LocationKind
    address: str | None
    meeting_link: str | None
    needs_confirmation: bool
    min_notice_minutes: int
    max_advance_days: int
    change_cutoff_minutes: int
    is_active: bool
    version: int
    person_ids: list[UUID]
    resource_ids: list[UUID]


class BookingTypeIn(Contract):
    name: Name
    duration_minutes: int = Field(ge=1, le=1440)
    buffer_minutes: int = Field(default=0, ge=0, le=1440)
    audience: Audience = "customers"
    delivery: Delivery = "any"
    resource_rule: ResourceRule = "none"
    location_kind: LocationKind
    address: Address | None = None
    meeting_link: MeetingLink | None = None
    needs_confirmation: bool = False
    min_notice_minutes: int = Field(default=60, ge=0, le=525600)
    max_advance_days: int = Field(default=60, ge=0, le=730)
    change_cutoff_minutes: int = Field(default=120, ge=0, le=525600)
    person_ids: list[UUID] = Field(default_factory=list, max_length=200)
    resource_ids: list[UUID] = Field(default_factory=list, max_length=200)


class BookingTypePatch(_Patch):
    name: Name | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    buffer_minutes: int | None = Field(default=None, ge=0, le=1440)
    audience: Audience | None = None
    delivery: Delivery | None = None
    resource_rule: ResourceRule | None = None
    location_kind: LocationKind | None = None
    address: Address | None = None
    meeting_link: MeetingLink | None = None
    needs_confirmation: bool | None = None
    min_notice_minutes: int | None = Field(default=None, ge=0, le=525600)
    max_advance_days: int | None = Field(default=None, ge=0, le=730)
    change_cutoff_minutes: int | None = Field(default=None, ge=0, le=525600)
    person_ids: list[UUID] | None = Field(default=None, max_length=200)
    resource_ids: list[UUID] | None = Field(default=None, max_length=200)
    is_active: bool | None = None

    @model_validator(mode="after")
    def _not_null(self) -> BookingTypePatch:
        self.refuse_null(
            *(n for n in self.model_fields_set if n not in ("address", "meeting_link"))
        )
        return self


class BookingPeriod(Contract):
    weekday: int = Field(ge=0, le=6)  # Monday is 0
    opens: ClockTime  # wall-clock time in the company's own time zone
    closes: ClockTime  # up to 23:59, and later than opens


class BookingHoursIn(Contract):
    subject_kind: HoursKind = "company"
    subject_id: UUID | None = None
    periods: list[BookingPeriod] = Field(max_length=70)


class BookingHoursOut(Contract):
    subject_kind: HoursKind
    subject_id: UUID | None
    # True when nothing is stored: a company then has the default week, a person or room
    # follows the company's hours.
    is_default: bool
    periods: list[BookingPeriod]


class BookingClosedDayIn(Contract):
    day: date
    note: Note | None = None


class BookingClosedDayOut(Contract):
    day: date
    note: str | None


class BookingBlockIn(Contract):
    subject_kind: SubjectKind
    subject_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    note: Note | None = None


class BookingBlockOut(Contract):
    id: UUID
    subject_kind: SubjectKind
    subject_id: UUID
    starts_at: datetime
    ends_at: datetime
    note: str | None
    created_by: UUID


# --- booking, moving and the calendar ----------------------------------------------------------

Phone = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=3, max_length=30, pattern=r"^\+?[0-9][0-9 ()\-.]*$"
    ),
    AfterValidator(_one_line),
]


def _sane_time(value: datetime) -> datetime:
    """A year between 2000 and 2100, in the time zone given and in UTC. Anything outside it is a
    typo, and the far ends of the calendar overflow the date arithmetic.
    """
    if not 2000 <= value.year <= 2100 or not 2000 <= value.astimezone(UTC).year <= 2100:
        raise ValueError("must be a time between the years 2000 and 2100")
    return value


# The one time type on every booking and availability input: with an offset, and in a sane range.
BookingTime = Annotated[AwareDatetime, AfterValidator(_sane_time)]

BookingStatus = Literal["pending", "confirmed", "cancelled", "no_show"]
BookingKind = Literal["appointment", "meeting"]
# How the email went after a staff change: "sent" (all of it), "failed" (at least one did not go),
# "off" (mail is switched off) or "none" (nobody on the booking has an address).
EmailSummary = Literal["sent", "off", "failed", "none"]
# What the rules can say about a time. Never why: a person who is away is only "unavailable".
BookingFinding = Literal["outside_hours", "closed_day", "short_notice", "unavailable"]


class BookingGuestIn(Contract):
    """Someone outside the company on a meeting, kept by email address."""

    name: Name | None = None
    email: Email


class BookingWalkInIn(Contract):
    """A customer who is not in the contact list yet: a name and a phone number or an email."""

    name: Name
    phone: Phone | None = None
    email: Email | None = None

    @model_validator(mode="after")
    def _a_way_to_reach_them(self) -> BookingWalkInIn:
        if self.phone is None and self.email is None:
            raise ValueError("a walk-in needs a phone number or an email address")
        return self


class BookingIn(Contract):
    """A booking of a type (`type_id`), or a meeting (`title` and `minutes`)."""

    type_id: UUID | None = None
    title: Name | None = None
    minutes: int | None = Field(default=None, ge=1, le=1440)
    starts_at: BookingTime
    people: list[UUID] = Field(default_factory=list, max_length=50)
    resources: list[UUID] = Field(default_factory=list, max_length=50)
    contact_id: UUID | None = None
    walk_in: BookingWalkInIn | None = None
    guests: list[BookingGuestIn] = Field(default_factory=list, max_length=50)
    private: bool = False
    # Where a meeting happens. With a type, the type decides, so these stay empty.
    location_kind: LocationKind | None = None
    address: Address | None = None
    meeting_link: MeetingLink | None = None
    acknowledged: list[BookingFinding] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _a_type_or_a_meeting(self) -> BookingIn:
        if self.type_id is not None and (self.title is not None or self.minutes is not None):
            raise ValueError("a booking has a type, or a title and minutes, not both")
        if self.type_id is None and (self.title is None or self.minutes is None):
            raise ValueError("a meeting needs a title and minutes")
        if self.contact_id is not None and self.walk_in is not None:
            raise ValueError("give a contact or a walk-in, not both")
        place = (self.location_kind, self.address, self.meeting_link)
        if self.type_id is not None and any(value is not None for value in place):
            raise ValueError("the booking type decides where it happens")
        return self


class BookingMoveIn(Contract):
    """Move a booking to a new start, keeping its length, people and rooms."""

    version: int = Field(ge=1)
    starts_at: BookingTime
    acknowledged: list[BookingFinding] = Field(default_factory=list, max_length=4)


class BookingActionIn(Contract):
    """Cancel, confirm, decline or mark a no-show. A version, when sent, must still be current."""

    version: int | None = Field(default=None, ge=1)


class BookingPersonRef(Contract):
    id: UUID
    display_name: str
    is_active: bool


class BookingResourceRef(Contract):
    id: UUID
    name: str
    is_active: bool


class BookingGuestOut(Contract):
    name: str | None
    email: str


class BookingCustomerOut(Contract):
    id: UUID
    full_name: str | None
    phone: str | None
    email: str | None


class BookingOut(Contract):
    """A booking as one viewer may see it. Every read goes through `service.to_out`.

    A private meeting the viewer is not part of is `redacted`: the title is "Busy" and nothing but
    the times, status and version is shown. A read-only member sees no customer and no guests.
    """

    id: UUID
    status: BookingStatus
    kind: BookingKind
    title: str | None
    private: bool
    redacted: bool
    starts_at: datetime
    ends_at: datetime
    version: int
    type_id: UUID | None
    customer: BookingCustomerOut | None
    people: list[BookingPersonRef]
    resources: list[BookingResourceRef]
    guests: list[BookingGuestOut]
    location_kind: LocationKind | None
    address: str | None
    meeting_link: str | None
    # "unavailable" when a person on the booking now has approved leave over it. Never why.
    conflict: Literal["unavailable"] | None
    # Whether this viewer may move, cancel or otherwise change it, by the rule the change routes
    # enforce: an Admin may change any booking, staff what they created or attend, read-only none.
    # It says nothing about the booking's status.
    can_change: bool
    # Only on the answer to a staff change that sends mail (book, move, cancel, confirm, decline);
    # null on every read, on a no-show, and on a private meeting the viewer is not part of.
    email: EmailSummary | None = None


class BookingSlotOut(Contract):
    """A free time in UTC, with everyone and everything that could take it."""

    starts_at: datetime
    ends_at: datetime
    people: list[UUID]
    resources: list[UUID]


class BookingBusy(Contract):
    """A time when someone or somewhere is taken by a private meeting the viewer is not part of.

    Nothing here names the meeting or links two rows: no id, no version, no people.
    """

    starts_at: datetime
    ends_at: datetime
    label: Literal["Busy"]


class BookingCalendarOut(Contract):
    """The calendar read. `items` are bookings as the viewer may see them.

    Without a person or room filter a private meeting is an item, redacted, so an Admin can still
    move or cancel it. With a filter it is a `busy` time instead, because an id would let anyone
    list who is in the meeting by filtering one person at a time. Overlapping times can still hint
    that two busy times are one meeting.
    """

    items: list[BookingOut]
    busy: list[BookingBusy]
