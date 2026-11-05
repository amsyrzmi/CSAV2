"""What the booking agent tells the reply composer: facts that are true of the booking as it is.

The reply is written only from these, so each one states the real state (confirmed, not confirmed
yet, cancelled) read from the booking itself, never from the code of the call alone: a retried
message answers `replay` with whatever the booking has become since. Every time is `format_slot`,
in the company's zone. A fact carries only what a customer may see of a booking: what, when,
where, the link, the names of the people booked and the status. Never another attendee's address,
a guest, a note, or why anyone is away.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from uuid import UUID
from zoneinfo import ZoneInfo

from csa.bookings.bot import BotOutcome, Offer, format_slot
from csa.bookings.contracts import BookingTypeOut
from csa.bookings.service import BookingRecord

TAKEN = "That time was just taken."
UNAVAILABLE = "Not available that day."
# The same words however the thing was missing: wrong id, someone else's, a private meeting.
NOT_FOUND = "That could not be found. Nothing was changed."
# Said when the bot does nothing at all, so that no booking is claimed.
OFF = "Booking by message is not available at the moment. Nothing was changed."
# These sentences are written to the customer, because when a reply cannot be checked they are
# sent as they stand: each must read properly on its own.
REMINDERS_NOW = " It will be used for reminders, including for the booking just made."
REMINDERS_LATER = " It will be used for reminders of future bookings."
NEEDS_NAME = (
    "I need your name to make the booking and there is none on file. Please tell me your name. "
    "Nothing has been booked yet."
)
EMAIL_NOT_SEEN = (
    "I could not find an email address in your message, so nothing was saved. Please type it again."
)
LIMIT = (
    "You have reached the number of upcoming bookings allowed, so no new booking was made. "
    "You can cancel one first."
)


def no_email(reminders: bool) -> str:
    """The ask for an email address after a booking made with none on file."""
    use = " It would be used for reminders." if reminders else ""
    return (
        "There is no email address on file for you, so no confirmation email will be sent. "
        f"If you would like to give one, tell me your email address.{use} It is fine to say no."
    )


# What the customer is told when a person has to decide, and the reason staff see.
HANDOFF: dict[str, tuple[str, str]] = {
    "past_cutoff": (
        "It is too close to the start time to change this booking by message. "
        "A team member will help.",
        "booking_change_past_cutoff",
    ),
    "overlaps_itself": (
        "The new time overlaps the booking's current time, which cannot be changed by message. "
        "A team member will help.",
        "booking_change_overlaps_itself",
    ),
}

LIVE = ("confirmed", "pending")


@dataclass(frozen=True)
class Scene:
    """What the facts need that the outcome does not carry."""

    zone: ZoneInfo
    type_names: Mapping[UUID, str] = field(default_factory=dict)
    original: BookingRecord | None = None  # the booking a pending request to move belongs to
    type_id: UUID | None = None  # the service the call was about, for naming fresh times
    ask_email: bool = False
    reminders: bool = False  # something that sends reminders is connected


def state_of(booking: BookingRecord) -> str:
    return {
        "confirmed": "confirmed",
        "pending": "not confirmed yet",
        "cancelled": "cancelled",
    }.get(booking.status, "no longer active")


def where(booking: BookingRecord) -> str | None:
    if booking.location_kind == "in_person":
        return f"at {booking.address}" if booking.address else "in person"
    if booking.location_kind == "phone":
        return "by phone"
    if booking.location_kind == "online":
        return "online"
    return None


def describe(booking: BookingRecord, scene: Scene) -> str:
    """What, when, with whom and where: the parts of a booking a customer may be told."""
    name = scene.type_names.get(booking.type_id) if booking.type_id else None
    parts = [f"{name or 'Booking'} on {format_slot(booking.starts_at, scene.zone)}"]
    names = [p.display_name for p in booking.people]
    if names:
        parts.append("with " + " and ".join(names))
    place = where(booking)
    if place:
        parts.append(place)
    return ", ".join(parts)


def link_of(booking: BookingRecord) -> str:
    """The meeting link of a booking that still stands, as the last words of its fact."""
    if booking.status in LIVE and booking.meeting_link:
        return f" Meeting link: {booking.meeting_link}"
    return ""


def standing(original: BookingRecord, scene: Scene) -> str:
    """The original booking a request to move belongs to, with its real time and state."""
    still = "still " if original.status in LIVE else ""
    return f"The original booking ({describe(original, scene)}) is {still}{state_of(original)}."


def booking_line(code: str, booking: BookingRecord, scene: Scene) -> str:
    """One booking, in the words its own state allows."""
    when = format_slot(booking.starts_at, scene.zone)
    if booking.replaces_booking_id is not None and scene.original is not None:
        if booking.status == "pending":
            return (
                f"Request to move the booking to {when} received. The new time is not confirmed "
                f"yet: the team has to confirm it. {standing(scene.original, scene)}"
            )
        if booking.status == "cancelled":
            return (
                f"The request to move the booking to {when} is withdrawn and will not go ahead. "
                f"{standing(scene.original, scene)}"
            )
    detail = describe(booking, scene)
    if booking.status == "confirmed":
        lead = "Booking moved and confirmed" if code == "moved" else "Booking confirmed"
        return f"{lead}: {detail}.{link_of(booking)}"
    if booking.status == "pending":
        lead = "Booking time changed" if code == "moved" else "Booking requested"
        return (
            f"{lead}: {detail}. It is not confirmed yet: the team has to confirm it."
            f"{link_of(booking)}"
        )
    if booking.status == "cancelled":
        return f"Booking cancelled: {detail}. It is cancelled."
    return f"Booking: {detail}. It is no longer active."


def times(offers: Iterable[Offer]) -> str:
    return "; ".join(o.label for o in offers)


def refusal(lead: str, offers: list[Offer], scene: Scene) -> str:
    """A refused time: the plain reason and other free times, never why it was refused."""
    if not offers:
        return f"{lead} No other free times were found soon."
    name = scene.type_names.get(scene.type_id) if scene.type_id else None
    return f"{lead} Other free times{f' for {name}' if name else ''}: {times(offers)}."


def withdrawn(booking: BookingRecord, scene: Scene) -> str:
    """The booking that stands after a request to move it was withdrawn."""
    if booking.status not in LIVE:
        return booking_line("change_withdrawn", booking, scene)
    return (
        "The request to move the booking is withdrawn. The booking stands as it was: "
        f"{describe(booking, scene)}. It is still {state_of(booking)}.{link_of(booking)}"
    )


def facts_for(outcome: BotOutcome, scene: Scene) -> list[str]:
    """The facts for one booking call, from the outcome and the booking's real state."""
    code, booking = outcome.code, outcome.booking
    if code in ("replies_off", "service_off"):
        return [OFF]
    if code == "slot_taken":
        return [refusal(TAKEN, outcome.offers, scene)]
    if code == "not_allowed":
        return [refusal(UNAVAILABLE, outcome.offers, scene)]
    if code == "limit_reached":
        return [LIMIT]
    if code in HANDOFF:
        return [HANDOFF[code][0]]
    if booking is None:
        return [NOT_FOUND]
    if code == "change_withdrawn":
        return [withdrawn(booking, scene)]
    lines = [booking_line(code, booking, scene)]
    if scene.ask_email and booking.status in LIVE:
        lines.append(no_email(scene.reminders))
    return lines


def saved_line(
    before: dict[str, str | None],
    after: dict[str, str | None],
    *,
    name: str | None,
    email: str | None,
    reminders: str = "",
) -> str:
    """What `save_details` did with the name and email just given, from the contact before and
    after. A detail already on file is never replaced. The address goes to `customer_given_email`
    and is always accepted when that is empty, so nothing here says whether another customer has
    it. `reminders` is what the saved address will be used for, which only the caller knows.
    """
    said: list[str] = []
    on_file: list[str] = []
    if name is not None:
        if not before.get("full_name") and after.get("full_name") == name:
            said.append("The name is saved.")
        else:
            on_file.append("a name")
    if email is not None:
        if before.get("email") or before.get("customer_given_email"):
            on_file.append("an email address")
        elif (after.get("customer_given_email") or "").casefold() == email.casefold():
            said.append("The email address is saved." + reminders)
        else:
            on_file.append("an email address")
    if not said:
        verb = "are" if len(on_file) > 1 else "is"
        return f"Nothing was changed: {' and '.join(on_file)} {verb} already on file."
    return " ".join(said)


def handoff_of(outcome: BotOutcome) -> str | None:
    """The reason staff see, for a call that needs a person."""
    found = HANDOFF.get(outcome.code)
    return found[1] if found else None


def type_line(booking_type: BookingTypeOut, *, ids: bool = True) -> str:
    """A service a customer may book. A lookup also gets the id its next call needs (not for the
    customer to see); a booking's own facts are sent to the customer as they stand, so no id.
    """
    key = f" (service id {booking_type.id}, for lookups only; never show it)" if ids else ""
    text = f"Can be booked: {booking_type.name}, {booking_type.duration_minutes} minutes{key}."
    if booking_type.needs_confirmation:
        text += " The team confirms these bookings first."
    return text
