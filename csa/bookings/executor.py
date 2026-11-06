"""The assistant's bookings: free times, and book, move or cancel as a card to confirm.

``booking_action`` follows ``subscription_action``: ``card`` runs at create and again at confirm,
and ``execute`` runs the same service paths the Bookings tab calls, so every rule is the tab's.
The model supplies words only. ``resolve`` finds the people, rooms, type, customer and booking
as the caller (staff change only their own or attended bookings; a private meeting the caller is
not part of is never found) and stores ids, never names. A time is the company's local day and
clock, turned into UTC here.

The card is a rehearsal: the service runs inside a transaction that is rolled back, so the card
shows exactly what Confirm will meet. Warnings (outside hours, a closed day, and for an Admin a
person away that day) are shown on the card and bound into its version, and confirming counts as
acknowledging those, no others. Leave is never explained, as the tab's rules have it: staff are
refused with "Not available that day.", and an Admin's card warns with that same sentence.

Idempotency: ``booking_turn_actions`` (made for customer messages) records the proposal's key
with the booking it made, moved or cancelled, so a replay finds that and acts no more.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException, Request
from pydantic import AwareDatetime, Field, ValidationError, model_validator

from csa.bookings import api as bookings_api
from csa.bookings import customer_messages, service, setup, turn_actions
from csa.bookings.bot import format_slot
from csa.bookings.contracts import BookingActionIn, BookingIn, BookingMoveIn
from csa.console.contracts import ConsoleActor
from csa.console.services import ACTIVE_ONLY, require_package
from csa.core import db
from csa.core.tenancy import tx
from csa.subscriptions import service as subscriptions
from csa.workspace.contracts import (
    AnswerProposal,
    BookingActionProposal,
    BookingFreeAsk,
    CardField,
    ClarificationOption,
    ClarificationProposal,
    Language,
    ProposalKind,
    WorkspaceTurnInput,
    _Contract,
)
from csa.workspace.executors import _CARD_TEXT, _key, _parse
from csa.workspace.planner import PlannerFacts
from csa.workspace.registry import (
    CardContent,
    PreparedProposal,
    ProposalActionRefused,
    ProposalError,
    ProposalOrigin,
    ProposalRegistry,
)
from csa.workspace.rules import TAB_GATES, unlocked

Resolved = AnswerProposal | ClarificationProposal | PreparedProposal
Conn = service.Conn

# The findings a confirm may acknowledge, because the card showed them. An Admin may also
# acknowledge "unavailable" (the owner's table: Admin gets a warning, then allowed); staff are
# refused it before it can be a warning. It is only ever the plain sentence, never why.
WARNINGS = ("closed_day", "outside_hours")
ADMIN_WARNINGS = (*WARNINGS, "unavailable")
MAX_OPTIONS = 5
OPTION_LABEL = 100
MAX_FREE_LINES = 6
# What a free-times answer assumes when nobody says how long the booking lasts.
DEFAULT_MINUTES = 30
LIVE = ("pending", "confirmed")
TURN_ACTION = {"book": "book", "move": "change", "cancel": "cancel"}
# A 403 while a card is made, by its code: the package suspended since the turn's facts were read,
# or a booking the caller may not change. Any other (a tenant mismatch) is the plain "can't".
FORBIDDEN = {"package_inactive": "bookings_unavailable", "own_bookings_only": "booking_own_only"}
MAIL: dict[str, Literal["created", "moved", "cancelled"]] = {
    "book": "created",
    "move": "moved",
    "cancel": "cancelled",
}


class BookingPayload(_Contract):
    """The stored payload: ids and one UTC time, never a name. The tab route accepts it too."""

    action: Literal["booking_action"]
    change: Literal["book", "move", "cancel"]
    type_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)
    minutes: int | None = Field(default=None, ge=1, le=1440)
    starts_at: AwareDatetime | None = None
    people: list[UUID] = Field(default_factory=list, max_length=50)
    resources: list[UUID] = Field(default_factory=list, max_length=50)
    contact_id: UUID | None = None
    booking_id: UUID | None = None

    @model_validator(mode="after")
    def shape(self) -> BookingPayload:
        if self.change == "book":
            valid = self.starts_at is not None and self.booking_id is None
        elif self.change == "move":
            valid = self.starts_at is not None and self.booking_id is not None
        else:
            valid = self.starts_at is None and self.booking_id is not None
        if not valid or (self.change != "book" and (self.type_id or self.title or self.minutes)):
            raise ValueError("payload does not fit its change")
        return self


def _payload_of(model: BookingPayload) -> dict[str, Any]:
    return model.model_dump(mode="json", exclude={"action"}, exclude_none=True)


# --- Names to ids, as the caller -------------------------------------------------------------


def _say(text: str) -> AnswerProposal:
    return AnswerProposal(action="answer", markdown=text)


def _ask(text: str, options: list[ClarificationOption] | None = None) -> ClarificationProposal:
    return ClarificationProposal(action="clarification", question=text, options=options or [])


def _as_id(text: str | None) -> UUID | None:
    if text is None:
        return None
    try:
        return UUID(text.strip())
    except ValueError:
        return None


def _words(text: str) -> list[str]:
    return [word for word in re.split(r"\W+", text.casefold()) if word]


def _matches(name: str, query: str) -> bool:
    """Every word of the query is in the name, in any order."""
    wanted = _words(query)
    folded = name.casefold()
    return bool(wanted) and all(word in folded for word in wanted)


@dataclass(frozen=True)
class Named:
    id: UUID
    name: str
    hint: str = ""


def _find(
    items: Sequence[Named], query: str, missing: str, which: str
) -> Named | AnswerProposal | ClarificationProposal:
    """One item, or what to tell the user: an id (a picked option) is exact, else a word search."""
    target = _as_id(query)
    if target is not None:
        found = next((item for item in items if item.id == target), None)
        return found if found is not None else _say(missing.format(name=query))
    hits = [item for item in items if _matches(item.name, query)]
    exact = [item for item in hits if item.name.casefold() == query.strip().casefold()]
    if len(exact) == 1:
        return exact[0]
    if not hits:
        return _say(missing.format(name=query))
    if len(hits) == 1:
        return hits[0]
    return _ask(
        which.format(name=query),
        [
            ClarificationOption(
                label=(f"{item.name} {item.hint}".strip())[:OPTION_LABEL], value=str(item.id)
            )
            for item in hits[:MAX_OPTIONS]
        ],
    )


def _clock(moment: datetime, zone: ZoneInfo) -> str:
    local = moment.astimezone(zone)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"


def _local_start(day: date, at: time, zone: ZoneInfo) -> datetime:
    return datetime.combine(day, at, tzinfo=zone).astimezone(UTC)


def _day_bounds(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    return _local_start(day, time(0), zone), _local_start(day + timedelta(days=1), time(0), zone)


def _record_names(record: service.BookingRecord, types: dict[UUID, str]) -> list[str]:
    names = [p.display_name for p in record.people] + [r.name for r in record.resources]
    if record.customer is not None and record.customer.full_name:
        names.append(record.customer.full_name)
    if record.title:
        names.append(record.title)
    if record.type_id is not None and record.type_id in types:
        names.append(types[record.type_id])
    return names


def _what(record: service.BookingRecord, types: dict[UUID, str], text: dict[str, str]) -> str:
    if record.private:
        return text["private_what"]
    named = types.get(record.type_id) if record.type_id else None
    return record.title or named or text["booking_what"]


async def _types(conn: Conn, client_id: UUID) -> dict[UUID, str]:
    return {t.id: t.name for t in await setup.list_types(conn, client_id)}


async def _customers(conn: Conn, client_id: UUID, query: str) -> list[Named]:
    target = _as_id(query)
    if target is not None:
        row = await db.q.fetch_one(
            "bookings/contact_get.sql", {"client_id": client_id, "id": target}, conn=conn
        )
        return [Named(target, query)] if row is not None else []
    found = await subscriptions.search_customers(conn, client_id, q=query, limit=MAX_OPTIONS + 1)
    return [Named(c.id, c.full_name or "?", _tail(c.phone)) for c in found]


def _tail(phone: str | None) -> str:
    """The last four digits, so two customers with one name can be told apart in a question."""
    digits = re.sub(r"\D", "", phone or "")
    return f"…{digits[-4:]}" if len(digits) >= 4 else ""


# --- Free times -------------------------------------------------------------------------------


async def free_lines(
    conn: Conn, client_id: UUID, ask: BookingFreeAsk, text: dict[str, str]
) -> list[str]:
    """The free times of one person or room on one local day, from the booking rules.

    Only free times: a busy time, a private meeting and leave all read as "not free", and
    nothing says why. Consecutive starts are shown as one stretch.
    """
    zone = await service.company_zone(conn, client_id)
    person = ask.person is not None
    if person:
        items = [Named(p.id, p.display_name) for p in await setup.list_people(conn, client_id)]
    else:
        items = [Named(r.id, r.name) for r in await setup.list_resources(conn, client_id)]
    active = {p.id for p in await setup.list_people(conn, client_id) if p.is_active}
    active |= {r.id for r in await setup.list_resources(conn, client_id) if r.is_active}
    query = ask.person or ask.room or ""
    found = _find(
        [item for item in items if item.id in active],
        query,
        text["booking_person_not_found" if person else "booking_room_not_found"],
        text["which_person"],
    )
    if isinstance(found, AnswerProposal):
        return [found.markdown]
    if isinstance(found, ClarificationProposal):
        names = ", ".join(option.label for option in found.options)
        return [f"{found.question} {names}"]
    start, end = _day_bounds(ask.day, zone)
    slots = await service.availability(
        conn,
        client_id,
        start=start,
        end=end,
        type_id=None,
        people=[found.id] if person else [],
        resources=[] if person else [found.id],
        minutes=ask.minutes or DEFAULT_MINUTES,
        limit=200,
    )
    stretches: list[list[datetime]] = []
    for slot in sorted(slots, key=lambda s: s.starts_at):
        if stretches and stretches[-1][1] >= slot.starts_at:
            stretches[-1][1] = max(stretches[-1][1], slot.ends_at)
        else:
            stretches.append([slot.starts_at, slot.ends_at])
    if not stretches:
        return [text["booking_free_none"].format(name=found.name)]
    return [
        text["booking_free_line"].format(
            name=found.name, start=format_slot(a, zone), end=_clock(b, zone)
        )
        for a, b in stretches[:MAX_FREE_LINES]
    ]


# --- Turn resolution --------------------------------------------------------------------------


async def resolve(
    request: Request,
    actor: ConsoleActor,
    client_id: UUID,
    turn: WorkspaceTurnInput,
    proposal: BookingActionProposal,
    facts: PlannerFacts,
    registry: ProposalRegistry,
    text: dict[str, str],
) -> Resolved:
    """A booking action, resolved as the caller; ``text`` is the turn's conversation copy."""
    if not unlocked(facts.packages, TAB_GATES["bookings"]):
        return _say(text["bookings_unavailable"])
    try:
        service.acting_as(actor)
    except HTTPException:
        return _say(text["bookings_read_only"])
    pool = request.app.state.pool
    async with tx(pool, str(client_id)) as conn:
        zone = await service.company_zone(conn, client_id)
        if proposal.change == "book":
            outcome = await _book(conn, client_id, actor, proposal, zone, text)
        else:
            outcome = await _change(conn, client_id, actor, proposal, zone, text)
    if not isinstance(outcome, BookingPayload):
        return outcome
    try:
        return await registry.prepare(
            request,
            actor,
            client_id,
            "booking_action",
            _payload_of(outcome),
            origin="assistant",
            language=facts.language,
        )
    except ProposalActionRefused as exc:
        code = exc.content["detail"]
        key = {"slot_taken": "booking_slot_taken", "refused": "booking_not_available"}
        return _say(text[key.get(str(code), "booking_cannot")])
    except ProposalError:
        raise
    except HTTPException as exc:
        if exc.status_code == 403:
            # The card's gate or the tab's rule refused it: say which, never another's sentence.
            return _say(text[FORBIDDEN.get(str(exc.detail), "booking_cannot")])
        if exc.status_code in (404, 409, 422):
            return _say(text["booking_cannot"])
        raise


async def _book(
    conn: Conn,
    client_id: UUID,
    actor: ConsoleActor,
    p: BookingActionProposal,
    zone: ZoneInfo,
    text: dict[str, str],
) -> BookingPayload | AnswerProposal | ClarificationProposal:
    types = [t for t in await setup.list_types(conn, client_id) if t.is_active]
    type_id: UUID | None = None
    named_only = True  # a meeting, or a type that names its person, needs someone said
    if p.booking_type is not None:
        found = _find(
            [Named(t.id, t.name) for t in types],
            p.booking_type,
            text["booking_type_not_found"],
            text["which_person"],
        )
        if not isinstance(found, Named):
            return found
        type_id = found.id
        named_only = next(t for t in types if t.id == found.id).delivery == "named"
    elif p.title is not None:
        if p.minutes is None:
            return _ask(text["booking_ask_minutes"])
    else:
        options = [
            ClarificationOption(label=t.name[:OPTION_LABEL], value=str(t.id))
            for t in types[:MAX_OPTIONS]
        ]
        return _ask(text["booking_ask_what"], options)
    if p.day is None or p.time is None:
        return _ask(text["booking_ask_when"])
    people_rows = [x for x in await setup.list_people(conn, client_id) if x.is_active]
    people: list[UUID] = []
    for name in p.people:
        found = _find(
            [Named(x.id, x.display_name) for x in people_rows],
            name,
            text["booking_person_not_found"],
            text["which_person"],
        )
        if not isinstance(found, Named):
            return found
        people.append(found.id)
    if p.include_me:
        mine = next((x for x in people_rows if x.user_id == actor.user_id), None)
        if mine is None:
            return _say(text["booking_me_not_bookable"])
        people.append(mine.id)
    rooms: list[UUID] = []
    room_rows = [Named(r.id, r.name) for r in await setup.list_resources(conn, client_id)]
    active_rooms = {r.id for r in await setup.list_resources(conn, client_id) if r.is_active}
    for name in p.rooms:
        found = _find(
            [r for r in room_rows if r.id in active_rooms],
            name,
            text["booking_room_not_found"],
            text["which_person"],
        )
        if not isinstance(found, Named):
            return found
        rooms.append(found.id)
    if not people and not rooms and named_only:
        return _ask(text["booking_ask_who"])
    contact: UUID | None = None
    if p.customer is not None:
        found = _find(
            await _customers(conn, client_id, p.customer),
            p.customer,
            text["booking_customer_not_found"],
            text["which_person"],
        )
        if not isinstance(found, Named):
            return found
        contact = found.id
    return _payload(
        change="book",
        type_id=type_id,
        title=None if type_id else p.title,
        minutes=None if type_id else p.minutes,
        starts_at=_local_start(p.day, time.fromisoformat(p.time), zone),
        people=list(dict.fromkeys(people)),
        resources=list(dict.fromkeys(rooms)),
        contact_id=contact,
    )


def _payload(**fields: Any) -> BookingPayload:
    try:
        return BookingPayload.model_validate({"action": "booking_action", **fields})
    except ValidationError:
        raise HTTPException(422, "invalid proposal payload") from None


async def _change(
    conn: Conn,
    client_id: UUID,
    actor: ConsoleActor,
    p: BookingActionProposal,
    zone: ZoneInfo,
    text: dict[str, str],
) -> BookingPayload | AnswerProposal | ClarificationProposal:
    types = await _types(conn, client_id)
    viewer = service.viewer_of(actor)
    if p.booking_id is not None:
        target = _as_id(p.booking_id)
        if target is None:
            return _say(text["booking_not_found"])
        candidates = await service.read_records(conn, client_id, booking_id=target)
    else:
        if p.on_day is None:
            return _ask(text["booking_ask_day"])
        start, end = _day_bounds(p.on_day, zone)
        candidates = await service.read_records(conn, client_id, start=start, end=end)
    # What the caller may change and may see: never a private meeting they are not part of.
    candidates = [
        r
        for r in candidates
        if r.status in LIVE
        and r.replaces_booking_id is None
        and not service.hidden_from(r, viewer)
        and service.may_change(r, viewer)
    ]
    if p.booking_id is None:
        if p.at_time is not None:
            candidates = [
                r for r in candidates if r.starts_at.astimezone(zone).strftime("%H:%M") == p.at_time
            ]
        if p.about is not None:
            about = p.about
            candidates = [
                r for r in candidates if any(_matches(n, about) for n in _record_names(r, types))
            ]
    if not candidates:
        return _say(text["booking_not_found"])
    if len(candidates) > 1:
        return _ask(
            text["booking_which_booking"],
            [
                ClarificationOption(label=_option_label(r, types, zone, text), value=str(r.id))
                for r in sorted(candidates, key=lambda r: r.starts_at)[:MAX_OPTIONS]
            ],
        )
    record = candidates[0]
    if p.change == "cancel":
        return _payload(change="cancel", booking_id=record.id)
    if p.time is None:
        return _ask(text["booking_ask_new_time"])
    day = p.day or record.starts_at.astimezone(zone).date()
    return _payload(
        change="move",
        booking_id=record.id,
        starts_at=_local_start(day, time.fromisoformat(p.time), zone),
    )


def _option_label(
    record: service.BookingRecord, types: dict[UUID, str], zone: ZoneInfo, text: dict[str, str]
) -> str:
    local = record.starts_at.astimezone(zone)
    when = f"{local:%a} {local.day} {local:%b}, {_clock(record.starts_at, zone)}"
    parts = [when, _what(record, types, text)]
    if not record.private:
        parts += [p.display_name for p in record.people][:2]
    return " · ".join(parts)[:OPTION_LABEL]


# --- The card and Confirm ---------------------------------------------------------------------


@dataclass(frozen=True)
class _Seen:
    """What a rehearsal saw, read inside its transaction."""

    record: service.BookingRecord
    types: dict[UUID, str]
    zone: ZoneInfo
    old: service.BookingRecord | None = None


class _Done(Exception):
    """Raised inside a transaction to roll it back once the rehearsal has what it came for."""

    def __init__(self, seen: _Seen) -> None:
        self.seen = seen


Rehearsal = Callable[[Conn, list[str]], Awaitable[_Seen]]


def _refusal(exc: service.Refusal) -> HTTPException:
    """A service refusal as Confirm's: a changed booking is a stale card, else its own body."""
    if exc.body.get("detail") in ("stale", "needs_acknowledging"):
        return ProposalError("proposal_stale")
    return ProposalActionRefused(exc.status_code, exc.body)


def _not_available() -> ProposalActionRefused:
    return ProposalActionRefused(
        409,
        {
            "detail": "refused",
            "message": service.NOT_AVAILABLE,
            "findings": ["unavailable"],
        },
    )


async def _rehearse(
    request: Request, client_id: UUID, run: Rehearsal, *, admin: bool = False
) -> tuple[_Seen, tuple[str, ...]]:
    """Run ``run`` in a transaction that is rolled back; the warnings it needed, with the sight.

    The first run acknowledges nothing: a refusal is raised plainly, and a time that needs
    acknowledging yields its findings. Hours and a closed day are acknowledged by Confirm. Leave
    (`unavailable`) is too, for an Admin only, whose card warns "Not available that day."; staff
    are refused it.
    """

    async def attempt(acknowledged: list[str]) -> _Seen:
        try:
            async with tx(request.app.state.pool, str(client_id)) as conn:
                raise _Done(await run(conn, acknowledged))
        except _Done as done:
            return done.seen

    try:
        return await attempt([]), ()
    except service.Refusal as exc:
        if exc.body.get("detail") != "needs_acknowledging":
            raise _refusal(exc) from None
        listed = exc.body.get("findings")
        findings = [str(f) for f in listed] if isinstance(listed, list) else []
        if not set(findings) <= set(ADMIN_WARNINGS if admin else WARNINGS):
            raise _not_available() from None
        try:
            return await attempt(findings), tuple(sorted(findings))
        except service.Refusal as again:
            raise _refusal(again) from None


async def _owned(
    conn: Conn, client_id: UUID, actor: ConsoleActor, booking_id: UUID
) -> service.BookingRecord:
    """The caller's booking to move or cancel, as the tab's rules allow."""
    found = await service.read_records(conn, client_id, booking_id=booking_id)
    viewer = service.viewer_of(actor)
    if not found or service.hidden_from(found[0], viewer):
        raise HTTPException(404, "booking_not_found")
    record = found[0]
    if not service.may_change(record, viewer):
        raise HTTPException(403, "own_bookings_only")
    if record.status not in LIVE or record.replaces_booking_id is not None:
        raise HTTPException(409, "wrong_status")
    return record


def _version(
    change: str, record: service.BookingRecord, warnings: Sequence[str], seen: _Seen
) -> str:
    """What a card binds: a booking's version, the warnings the card showed, and for a new
    booking the people and rooms it would hold (a pool is picked, so the pick is bound too)."""
    if change == "cancel":
        return str(record.version)
    ack = ",".join(warnings)
    if change == "move":
        return f"{record.version};ack={ack}"
    people = ",".join(sorted(str(p.person_id) for p in seen.record.people))
    rooms = ",".join(sorted(str(r.id) for r in seen.record.resources))
    return f"book;ack={ack};people={people};rooms={rooms}"


def _bound(expected: str | None) -> dict[str, str]:
    if expected is None:
        raise RuntimeError("booking_action proposal has no bound version")
    head, *rest = expected.split(";")
    found = dict(part.split("=", 1) for part in rest)
    return {"head": head, **found}


def _fields(
    change: str,
    seen: _Seen,
    warnings: Sequence[str],
    text: dict[str, str],
) -> list[CardField]:
    record, zone = seen.record, seen.zone
    field = [CardField(label=text["label_what"], value=_what(record, seen.types, text))]
    if change == "move" and seen.old is not None:
        field.append(
            CardField(label=text["label_from"], value=format_slot(seen.old.starts_at, zone))
        )
        field.append(CardField(label=text["label_to"], value=format_slot(record.starts_at, zone)))
    else:
        field.append(CardField(label=text["label_when"], value=format_slot(record.starts_at, zone)))
    if record.private:
        return field  # the card is stored where others may read: nothing but what and when
    if change == "book":
        minutes = int((record.ends_at - record.starts_at).total_seconds() // 60)
        field.append(
            CardField(label=text["label_length"], value=text["minutes"].format(count=minutes))
        )
    if record.people:
        field.append(
            CardField(
                label=text["label_with"], value=", ".join(p.display_name for p in record.people)
            )
        )
    if record.resources:
        field.append(
            CardField(label=text["label_room"], value=", ".join(r.name for r in record.resources))
        )
    where = _place(record, text)
    if where:
        field.append(CardField(label=text["label_where"], value=where))
    if record.customer is not None:
        name = record.customer.full_name or text["unnamed_customer"]
        field.append(CardField(label=text["label_customer"], value=name))
    if warnings:
        field.append(
            CardField(
                label=text["label_warning"], value=" ".join(text[f"warn_{w}"] for w in warnings)
            )
        )
    return field


def _place(record: service.BookingRecord, text: dict[str, str]) -> str | None:
    if record.location_kind == "online":
        return record.meeting_link
    if record.location_kind == "in_person":
        return record.address
    return text["where_phone"] if record.location_kind == "phone" else None


class BookingActionExecutor:
    kind: ProposalKind = "booking_action"

    async def gate(
        self, request: Request, actor: ConsoleActor, client_id: UUID, *, origin: ProposalOrigin
    ) -> None:
        """Staff or an Admin of this company (a platform admin only for the company they chose)
        with an active ``bookings`` package, for both origins. A read-only member gets no card."""
        if not actor.platform_admin and actor.client_id != client_id:
            raise HTTPException(403, "bookings tenant is not authorized")
        service.acting_as(actor)
        await require_package(
            request.app.state.pool, client_id, frozenset({"bookings"}), ACTIVE_ONLY
        )

    async def card(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        origin: ProposalOrigin,
        language: Language = "en",
    ) -> tuple[CardContent, str | None]:
        data = _parse(BookingPayload, self.kind, payload)
        await self.gate(request, actor, client_id, origin=origin)
        text = _CARD_TEXT[language]
        run = self._rehearsal(client_id, actor, data)
        admin = service.acting_as(actor) == "admin"
        seen, warnings = await _rehearse(request, client_id, run, admin=admin)
        record = seen.old or seen.record
        when = format_slot(seen.record.starts_at, seen.zone)
        what = _what(seen.record, seen.types, text)
        title = text[f"{data.change}_title"].format(what=what)
        summary = text[f"{data.change}_summary"].format(when=when)
        return (
            CardContent(
                title=title,
                summary=summary,
                fields=_fields(data.change, seen, warnings, text),
                language=language,
            ),
            _version(data.change, record, warnings, seen),
        )

    @staticmethod
    def _rehearsal(client_id: UUID, actor: ConsoleActor, data: BookingPayload) -> Rehearsal:
        async def run(conn: Conn, acknowledged: list[str]) -> _Seen:
            zone = await service.company_zone(conn, client_id)
            types = await _types(conn, client_id)
            if data.change == "book":
                record = await service.create_for(
                    conn, client_id, _author(actor), _booking_in(data, acknowledged)
                )
                return _Seen(record, types, zone)
            assert data.booking_id is not None
            old = await _owned(conn, client_id, actor, data.booking_id)
            if data.change == "cancel":
                return _Seen(old, types, zone, old)
            assert data.starts_at is not None
            moved = await service.move_booking(
                conn,
                client_id,
                actor,
                data.booking_id,
                BookingMoveIn(
                    version=old.version,
                    starts_at=data.starts_at,
                    acknowledged=acknowledged,  # type: ignore[arg-type]
                ),
            )
            return _Seen(moved, types, zone, old)

        return run

    async def execute(
        self,
        request: Request,
        actor: ConsoleActor,
        client_id: UUID,
        payload: dict[str, Any],
        *,
        proposal_id: UUID,
        expected_version: str | None,
        origin: ProposalOrigin,
    ) -> dict[str, Any]:
        data = _parse(BookingPayload, self.kind, payload)
        bound = _bound(expected_version)
        acknowledged = [w for w in bound.get("ack", "").split(",") if w]
        turn_key = f"assistant:{_key(proposal_id, 'booking')}"
        by_chat = False
        original: service.BookingRecord | None = None
        invite = True
        try:
            async with tx(request.app.state.pool, str(client_id)) as conn:
                done = await turn_actions.done_by(conn, client_id, turn_key)
                if done is not None:
                    # Replayed: the booking the first run made, moved or cancelled, as it is now.
                    found = await service.read_records(
                        conn, client_id, booking_id=done["booking_id"]
                    )
                    return _receipt(data.change, found[0])
                record = await self._apply(conn, client_id, actor, data, bound, acknowledged)
                await turn_actions.remember(
                    conn, client_id, turn_key, record.id, TURN_ACTION[data.change]
                )
                if data.change != "book":
                    by_chat = await customer_messages.after_staff_action(
                        conn, client_id, record, MAIL[data.change]
                    )
                    original, invite = await customer_messages.mail_details(
                        conn, client_id, record, MAIL[data.change]
                    )
        except service.Refusal as exc:
            raise _refusal(exc) from None
        # The same mail step the tab runs, after the commit: it never raises.
        outcome = await bookings_api.after_commit(
            request, record, MAIL[data.change], by_chat=by_chat, original=original, invite=invite
        )
        receipt = _receipt(data.change, record)
        if outcome is not None and not record.private:
            receipt["email"] = outcome.email
            if outcome.customer_told is not None:
                receipt["customer_told"] = outcome.customer_told
        return receipt

    @staticmethod
    async def _apply(
        conn: Conn,
        client_id: UUID,
        actor: ConsoleActor,
        data: BookingPayload,
        bound: dict[str, str],
        acknowledged: list[str],
    ) -> service.BookingRecord:
        if data.change == "book":
            record = await service.create_for(
                conn, client_id, _author(actor), _booking_in(data, acknowledged)
            )
            crew = (
                ",".join(sorted(str(p.person_id) for p in record.people)),
                ",".join(sorted(str(r.id) for r in record.resources)),
            )
            if crew != (bound.get("people", ""), bound.get("rooms", "")):
                raise ProposalError("proposal_stale")  # another person was picked since the card
            return record
        assert data.booking_id is not None
        version = int(bound["head"])
        if data.change == "move":
            assert data.starts_at is not None
            return await service.move_booking(
                conn,
                client_id,
                actor,
                data.booking_id,
                BookingMoveIn(
                    version=version,
                    starts_at=data.starts_at,
                    acknowledged=acknowledged,  # type: ignore[arg-type]
                ),
            )
        return await service.cancel_booking(
            conn, client_id, actor, data.booking_id, BookingActionIn(version=version)
        )


def _author(actor: ConsoleActor) -> service.Author:
    """Staff or Admin rules, recorded as the assistant's."""
    return service.Author(service.acting_as(actor), actor.user_id, "assistant")


def _booking_in(data: BookingPayload, acknowledged: list[str]) -> BookingIn:
    try:
        return BookingIn(
            type_id=data.type_id,
            title=data.title,
            minutes=data.minutes,
            starts_at=data.starts_at,  # type: ignore[arg-type]
            people=data.people,
            resources=data.resources,
            contact_id=data.contact_id,
            acknowledged=acknowledged,  # type: ignore[arg-type]
        )
    except ValidationError:
        raise HTTPException(422, "invalid proposal payload") from None


def _receipt(change: str, record: service.BookingRecord) -> dict[str, Any]:
    """Ids, status and time only: the proposal row is readable across the company."""
    return {
        "change": change,
        "booking_id": str(record.id),
        "status": record.status,
        "starts_at": record.starts_at.isoformat(),
        "version": record.version,
    }
