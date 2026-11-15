"""What the booking agent tells the reply composer: facts that are true of the booking as it is.

The reply is written only from these, so each one states the real state (confirmed, not confirmed
yet, cancelled) read from the booking itself, never from the code of the call alone: a retried
message answers `replay` with whatever the booking has become since. Every time is `format_slot`,
in the company's zone. A fact carries only what a customer may see of a booking: what, when,
where, the link, the names of the people booked and the status. Never another attendee's address,
a guest, a note, or why anyone is away.

A write's facts can go out word for word (beside a payment answer, or when the reply cannot be
composed), so each builder also words them in Malay (`ms=True`, times by `format_slot_ms`): the
agent puts that in `Fact.text_ms` for a Malay turn. The English is what the composer and grounding
read, and the Malay holds the same numbers. Service and people's names, addresses and links stay
as stored.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from uuid import UUID
from zoneinfo import ZoneInfo

from csa.bookings.bot import BotOutcome, Offer, format_slot, format_slot_ms
from csa.bookings.contracts import BookingTypeOut
from csa.bookings.service import BookingRecord

TAKEN = "That time was just taken."
TAKEN_MS = "Masa tu baru je ditempah orang lain."
UNAVAILABLE = "Not available that day."
UNAVAILABLE_MS = "Hari tu tak boleh ditempah."
# The same words however the thing was missing: wrong id, someone else's, a private meeting.
NOT_FOUND = "That could not be found. Nothing was changed."
NOT_FOUND_MS = "Tak jumpa yang tu. Tak ada apa-apa yang diubah."
# Said when the bot does nothing at all, so that no booking is claimed.
OFF = "Booking by message is not available at the moment. Nothing was changed."
OFF_MS = "Buat masa ni tak boleh tempah melalui mesej. Tak ada apa-apa yang diubah."
# These sentences are written to the customer, because when a reply cannot be checked they are
# sent as they stand: each must read properly on its own.
REMINDERS_NOW = " It will be used for reminders, including for the booking just made."
REMINDERS_NOW_MS = (
    " Kami akan guna e-mel tu untuk peringatan, termasuk untuk tempahan yang baru dibuat tadi."
)
REMINDERS_LATER = " It will be used for reminders of future bookings."
REMINDERS_LATER_MS = " Kami akan guna e-mel tu untuk peringatan tempahan akan datang."
NEEDS_NAME = (
    "I need your name to make the booking and there is none on file. Please tell me your name. "
    "Nothing has been booked yet."
)
NEEDS_NAME_MS = (
    "Saya perlukan nama awak untuk buat tempahan, dan nama awak tak ada dalam rekod. "
    "Bagitahu nama awak ya. Belum ada apa-apa yang ditempah lagi."
)
EMAIL_NOT_SEEN = (
    "I could not find an email address in your message, so nothing was saved. Please type it again."
)
EMAIL_NOT_SEEN_MS = (
    "Saya tak jumpa alamat e-mel dalam mesej awak, jadi tak ada apa-apa yang disimpan. "
    "Taip sekali lagi ya."
)
LIMIT = (
    "You have reached the number of upcoming bookings allowed, so no new booking was made. "
    "You can cancel one first."
)
LIMIT_MS = (
    "Awak dah cukup had tempahan akan datang yang dibenarkan, jadi tempahan baru tak dibuat. "
    "Awak boleh batalkan satu dulu."
)


def no_email(reminders: bool, *, ms: bool = False) -> str:
    """The ask for an email address after a booking made with none on file."""
    if ms:
        use = " Kami akan guna e-mel tu untuk peringatan." if reminders else ""
        return (
            "Alamat e-mel awak tak ada dalam rekod, jadi e-mel pengesahan takkan dihantar. "
            f"Kalau awak nak bagi, bagitahu alamat e-mel awak.{use} Tak nak pun tak apa."
        )
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
HANDOFF_MS: dict[str, str] = {
    "past_cutoff": (
        "Dah terlalu dekat dengan masa mula untuk ubah tempahan ni melalui mesej. "
        "Staf kami akan tolong awak."
    ),
    "overlaps_itself": (
        "Masa baru tu bertindih dengan masa tempahan sekarang, jadi tak boleh diubah melalui "
        "mesej. Staf kami akan tolong awak."
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


STATES = {"confirmed": "confirmed", "pending": "not confirmed yet", "cancelled": "cancelled"}
STATES_MS = {"confirmed": "disahkan", "pending": "belum disahkan", "cancelled": "dibatalkan"}


def state_of(booking: BookingRecord, *, ms: bool = False) -> str:
    if ms:
        return STATES_MS.get(booking.status, "dah tak aktif")
    return STATES.get(booking.status, "no longer active")


PLACES = {"in_person": "in person", "phone": "by phone", "online": "online"}
PLACES_MS = {"in_person": "secara bersemuka", "phone": "melalui telefon", "online": "secara online"}


def where(booking: BookingRecord, *, ms: bool = False) -> str | None:
    if booking.location_kind == "in_person" and booking.address:
        return f"{'kat' if ms else 'at'} {booking.address}"
    if booking.location_kind is None:
        return None
    return (PLACES_MS if ms else PLACES).get(booking.location_kind)


def describe(booking: BookingRecord, scene: Scene, *, ms: bool = False) -> str:
    """What, when, with whom and where: the parts of a booking a customer may be told."""
    name = scene.type_names.get(booking.type_id) if booking.type_id else None
    if ms:
        parts = [f"{name or 'Tempahan'} pada {format_slot_ms(booking.starts_at, scene.zone)}"]
    else:
        parts = [f"{name or 'Booking'} on {format_slot(booking.starts_at, scene.zone)}"]
    names = [p.display_name for p in booking.people]
    if names:
        parts.append(("dengan " + " dan ".join(names)) if ms else ("with " + " and ".join(names)))
    place = where(booking, ms=ms)
    if place:
        parts.append(place)
    return ", ".join(parts)


def link_of(booking: BookingRecord, *, ms: bool = False) -> str:
    """The meeting link of a booking that still stands, as the last words of its fact."""
    if booking.status in LIVE and booking.meeting_link:
        return f" {'Link meeting' if ms else 'Meeting link'}: {booking.meeting_link}"
    return ""


def standing(original: BookingRecord, scene: Scene, *, ms: bool = False) -> str:
    """The original booking a request to move belongs to, with its real time and state."""
    if ms:
        still = "masih " if original.status in LIVE else ""
        what = describe(original, scene, ms=True)
        return f"Tempahan asal ({what}) {still}{state_of(original, ms=True)}."
    still = "still " if original.status in LIVE else ""
    return f"The original booking ({describe(original, scene)}) is {still}{state_of(original)}."


def booking_line(code: str, booking: BookingRecord, scene: Scene, *, ms: bool = False) -> str:
    """One booking, in the words its own state allows."""
    if ms:
        return _booking_line_ms(code, booking, scene)
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


def _booking_line_ms(code: str, booking: BookingRecord, scene: Scene) -> str:
    """`booking_line` in Malay, case for case."""
    when = format_slot_ms(booking.starts_at, scene.zone)
    if booking.replaces_booking_id is not None and scene.original is not None:
        stands = standing(scene.original, scene, ms=True)
        if booking.status == "pending":
            return (
                f"Permintaan nak tukar tempahan ke {when} dah diterima. Masa baru tu belum "
                f"disahkan lagi: staf kami kena sahkan dulu. {stands}"
            )
        if booking.status == "cancelled":
            return (
                f"Permintaan nak tukar tempahan ke {when} dah ditarik balik dan takkan "
                f"diteruskan. {stands}"
            )
    detail, link = describe(booking, scene, ms=True), link_of(booking, ms=True)
    if booking.status == "confirmed":
        lead = "Tempahan dah ditukar dan disahkan" if code == "moved" else "Tempahan dah disahkan"
        return f"{lead}: {detail}.{link}"
    if booking.status == "pending":
        lead = (
            "Masa tempahan dah ditukar" if code == "moved" else "Permintaan tempahan dah diterima"
        )
        return f"{lead}: {detail}. Belum disahkan lagi: staf kami kena sahkan dulu.{link}"
    if booking.status == "cancelled":
        return f"Tempahan dah dibatalkan: {detail}. Tempahan ni dah batal."
    return f"Tempahan: {detail}. Tempahan ni dah tak aktif."


def times(offers: Iterable[Offer], *, ms: bool = False) -> str:
    return "; ".join((o.label_ms or o.label) if ms else o.label for o in offers)


def refusal(lead: str, offers: list[Offer], scene: Scene, *, ms: bool = False) -> str:
    """A refused time: the plain reason and other free times, never why it was refused."""
    name = scene.type_names.get(scene.type_id) if scene.type_id else None
    if ms:
        if not offers:
            return f"{lead} Tak jumpa masa kosong lain dalam masa terdekat."
        return (
            f"{lead} Masa kosong lain{f' untuk {name}' if name else ''}: {times(offers, ms=True)}."
        )
    if not offers:
        return f"{lead} No other free times were found soon."
    return f"{lead} Other free times{f' for {name}' if name else ''}: {times(offers)}."


def withdrawn(booking: BookingRecord, scene: Scene, *, ms: bool = False) -> str:
    """The booking that stands after a request to move it was withdrawn."""
    if booking.status not in LIVE:
        return booking_line("change_withdrawn", booking, scene, ms=ms)
    if ms:
        return (
            "Permintaan nak tukar tempahan dah ditarik balik. Tempahan kekal macam asal: "
            f"{describe(booking, scene, ms=True)}. Tempahan ni masih "
            f"{state_of(booking, ms=True)}.{link_of(booking, ms=True)}"
        )
    return (
        "The request to move the booking is withdrawn. The booking stands as it was: "
        f"{describe(booking, scene)}. It is still {state_of(booking)}.{link_of(booking)}"
    )


def cancel_question(booking: BookingRecord, scene: Scene, *, ms: bool = False) -> str:
    """The question a cancel is asked as: what and when, and that nothing has happened yet.
    The customer answers in words (the planner reads a yes in any language), not with a button.
    """
    name = scene.type_names.get(booking.type_id) if booking.type_id else None
    if ms:
        what, when = name or "tempahan", format_slot_ms(booking.starts_at, scene.zone)
        if booking.replaces_booking_id is not None:
            return (
                f"Nak tarik balik permintaan tukar {what} awak ke {when}? Balas ya untuk tarik "
                "balik. Belum ada apa-apa yang diubah lagi."
            )
        return (
            f"Nak batalkan {what} awak pada {when}? Balas ya untuk batalkan. "
            "Belum ada apa-apa yang dibatalkan lagi."
        )
    when = format_slot(booking.starts_at, scene.zone)
    if booking.replaces_booking_id is not None:
        return (
            f"Withdraw your request to move your {name or 'booking'} to {when}? Reply yes to "
            "withdraw it. Nothing has been changed yet."
        )
    return (
        f"Cancel your {name or 'booking'} on {when}? Reply yes to cancel. "
        "Nothing has been cancelled yet."
    )


def facts_for(outcome: BotOutcome, scene: Scene, *, ms: bool = False) -> list[str]:
    """The facts for one booking call, from the outcome and the booking's real state: in English,
    or with `ms` the same facts in Malay, one for one."""
    code, booking = outcome.code, outcome.booking
    if code in ("replies_off", "service_off"):
        return [OFF_MS if ms else OFF]
    if code == "slot_taken":
        return [refusal(TAKEN_MS if ms else TAKEN, outcome.offers, scene, ms=ms)]
    if code == "not_allowed":
        return [refusal(UNAVAILABLE_MS if ms else UNAVAILABLE, outcome.offers, scene, ms=ms)]
    if code == "limit_reached":
        return [LIMIT_MS if ms else LIMIT]
    if code in HANDOFF:
        return [HANDOFF_MS[code] if ms else HANDOFF[code][0]]
    if booking is None:
        return [NOT_FOUND_MS if ms else NOT_FOUND]
    if code == "change_withdrawn":
        return [withdrawn(booking, scene, ms=ms)]
    if code == "cancel_asked":
        return [cancel_question(booking, scene, ms=ms)]
    lines = [booking_line(code, booking, scene, ms=ms)]
    if scene.ask_email and booking.status in LIVE:
        lines.append(no_email(scene.reminders, ms=ms))
    return lines


def saved_line(
    before: dict[str, str | None],
    after: dict[str, str | None],
    *,
    name: str | None,
    email: str | None,
    reminders: str = "",
    ms: bool = False,
) -> str:
    """What `save_details` did with the name and email just given, from the contact before and
    after. A detail already on file is never replaced. The address goes to `customer_given_email`
    and is always accepted when that is empty, so nothing here says whether another customer has
    it. `reminders` is what the saved address will be used for, which only the caller knows (in
    the language asked for: `REMINDERS_NOW` or `REMINDERS_NOW_MS`, and so on).
    """
    said: list[str] = []
    on_file: list[str] = []
    a_name, an_email = ("nama", "alamat e-mel") if ms else ("a name", "an email address")
    if name is not None:
        if not before.get("full_name") and after.get("full_name") == name:
            said.append("Nama dah disimpan." if ms else "The name is saved.")
        else:
            on_file.append(a_name)
    if email is not None:
        if before.get("email") or before.get("customer_given_email"):
            on_file.append(an_email)
        elif (after.get("customer_given_email") or "").casefold() == email.casefold():
            said.append(
                ("Alamat e-mel dah disimpan." if ms else "The email address is saved.") + reminders
            )
        else:
            on_file.append(an_email)
    if not said:
        if ms:
            return f"Tak ada apa-apa yang diubah: {' dan '.join(on_file)} dah ada dalam rekod."
        verb = "are" if len(on_file) > 1 else "is"
        return f"Nothing was changed: {' and '.join(on_file)} {verb} already on file."
    return " ".join(said)


def handoff_of(outcome: BotOutcome) -> str | None:
    """The reason staff see, for a call that needs a person."""
    found = HANDOFF.get(outcome.code)
    return found[1] if found else None


def type_line(booking_type: BookingTypeOut, *, ids: bool = True, ms: bool = False) -> str:
    """A service a customer may book. A lookup also gets the id its next call needs (not for the
    customer to see); a booking's own facts are sent to the customer as they stand, so no id, and
    those (`ms`) can be told in Malay.
    """
    if ms:
        text = f"Boleh ditempah: {booking_type.name}, {booking_type.duration_minutes} minit."
        if booking_type.needs_confirmation:
            text += " Staf kami sahkan tempahan jenis ni dulu."
        return text
    key = f" (service id {booking_type.id}, for lookups only; never show it)" if ids else ""
    text = f"Can be booked: {booking_type.name}, {booking_type.duration_minutes} minutes{key}."
    if booking_type.needs_confirmation:
        text += " The team confirms these bookings first."
    return text
