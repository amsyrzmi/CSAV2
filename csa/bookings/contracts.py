"""Pydantic contracts for the bookings setup API: people, rooms, types, hours, days and blocks."""

from __future__ import annotations

import unicodedata
from datetime import date, datetime
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
