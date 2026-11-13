"""What a customer reads about an invoice, in English or Malay: pure functions, no database.

Every text is built from facts the server holds (the brand, the invoice's reference, amount, plan
and due date, a method's details and the instructions staff wrote for it) and nothing a customer
typed, so a reference, an amount or an account number is always inserted exactly as stored.
Staff-only fields (void reason, who voided, payment notes, a claim note) are never read here.

Amounts are written with `csa.agents.facts.money` ("MYR 1,059.90"). Dates are the local date in
the company's time zone, day, month name and year ("20 December 2026", "20 Disember 2026"): the
day of a booking reminder's date (`csa.bookings.bot.format_slot`), with no time, since an invoice
is due on a day.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from csa.agents.facts import money
from csa.payments.contracts import MethodOut
from csa.payments.invoices import InvoiceRecord

Locale = Literal["en", "ms"]
Channel = Literal["chat", "email"]

_MONTHS: dict[Locale, tuple[str, ...]] = {
    "en": (
        "January", "February", "March", "April", "May", "June", "July", "August", "September",
        "October", "November", "December",
    ),
    "ms": (
        "Januari", "Februari", "Mac", "April", "Mei", "Jun", "Julai", "Ogos", "September",
        "Oktober", "November", "Disember",
    ),
}  # fmt: skip


def locale_of(value: str | None) -> Locale:
    """`ms` for a Malay locale (`ms`, `ms-MY`, ...), else `en`."""
    return "ms" if value and value.lower().startswith("ms") else "en"


def _zone(tz: str | ZoneInfo) -> ZoneInfo:
    return tz if isinstance(tz, ZoneInfo) else ZoneInfo(tz)


def local_date(value: datetime, locale: Locale, tz: str | ZoneInfo) -> str:
    """The local date of `value` in the company's zone: "20 December 2026" / "20 Disember 2026"."""
    local = value.astimezone(_zone(tz))
    return f"{local.day} {_MONTHS[locale][local.month - 1]} {local.year}"


def _ref(inv: InvoiceRecord) -> str:
    # Invoices made before references existed carry none; their id is what staff see for them.
    return inv.reference or inv.id


def invoice_line(inv: InvoiceRecord, locale: Locale, tz: str | ZoneInfo) -> str:
    """One invoice in a sentence; an `overdue` invoice says its due date has passed.

    Without a plan name the "for ..." part is left out, and without a due date the due part.
    """
    amount = money(inv.amount, inv.currency)
    if locale == "ms":
        head = f"Invois {_ref(inv)}: {amount}"
        plan = f" untuk {inv.plan_name}" if inv.plan_name else ""
        if inv.due_at is None:
            return f"{head}{plan}."
        date = local_date(inv.due_at, locale, tz)
        if inv.status == "overdue":
            return f"{head}{plan}, tarikh akhir {date} dah lepas."
        return f"{head}{plan}, kena bayar sebelum {date}."
    head = f"Invoice {_ref(inv)}: {amount}"
    plan = f" for {inv.plan_name}" if inv.plan_name else ""
    if inv.due_at is None:
        return f"{head}{plan}."
    date = local_date(inv.due_at, locale, tz)
    if inv.status == "overdue":
        return f"{head}{plan}, was due {date}."
    return f"{head}{plan}, due {date}."


def method_line(m: MethodOut, locale: Locale) -> str:
    """How to pay with one method; the instructions staff wrote follow after one space."""
    details = m.details
    if m.kind == "transfer":
        if locale == "ms":
            line = (
                f"Pindahan bank: {details['bank_name']}, akaun {details['account_number']}, "
                f"nama {details['account_name']}."
            )
        else:
            line = (
                f"Bank transfer: {details['bank_name']}, account {details['account_number']}, "
                f"name {details['account_name']}."
            )
    elif m.kind == "qr":
        name = "nama" if locale == "ms" else "name"
        line = f"DuitNow: {details['duitnow_id']}, {name} {details['account_name']}."
    else:  # cash (Payments 2 pays it through a booking); listed by its label only
        line = f"{'Tunai' if locale == 'ms' else 'Cash'}: {m.label}."
    instructions = m.instructions.strip()
    return f"{line} {instructions}" if instructions else line


def reference_line(refs: Sequence[str], locale: Locale) -> str:
    """Asks for the reference with the payment: by name for one invoice, in general for several."""
    if len(set(refs)) == 1:
        if locale == "ms":
            return f"Letak {refs[0]} sebagai rujukan bayaran ya, senang kami padankan."
        return f"Please put {refs[0]} as the payment reference so we can match your payment."
    if locale == "ms":
        return "Letak rujukan setiap invois dengan bayarannya ya."
    return "Put each invoice's reference with its payment so we can match it."


def _first_line(
    inv: InvoiceRecord, *, today: date, brand: str, locale: Locale, tz: str | ZoneInfo
) -> str:
    """The first line, chosen by the real local due date against `today`, not by the step: a
    step-0 reminder held back a day by quiet hours or the daily cap says "was due on"."""
    amount = money(inv.amount, inv.currency)
    ref = _ref(inv)
    zone = _zone(tz)
    # An invoice without a due date (none is reminded) reads as due today.
    due = inv.due_at.astimezone(zone).date() if inv.due_at is not None else today
    when = local_date(inv.due_at, locale, zone) if inv.due_at is not None else ""
    if locale == "ms":
        head = f"{brand}: invois {ref} untuk {amount}"
        if due == today:
            return f"{head} kena bayar hari ini."
        if due < today:
            return f"{head}, tarikh akhir {when} dah lepas dan masih belum dibayar."
        return f"{head} kena bayar sebelum {when}."
    head = f"{brand}: invoice {ref} for {amount}"
    if due == today:
        return f"{head} is due today."
    if due < today:
        return f"{head} was due on {when} and is still unpaid."
    return f"{head} is due on {when}."


def reminder_text(
    inv: InvoiceRecord,
    methods: Sequence[MethodOut],
    *,
    today: date,
    brand: str,
    locale: Locale,
    tz: str | ZoneInfo,
    channel: Channel = "chat",
) -> str:
    """The reminder: what is owed and when, how to pay, the reference to use, and what to do if
    it is already paid. One line each. `today` is the local date, in the company's time zone, at
    the moment the text is built (the mail job passes its own at send time): the first line says
    "due today", "due on {date}" or "was due on {date} and is still unpaid" by comparing it with
    the invoice's local due date. Pure: nothing is read from the clock.

    `methods` are listed as given; the caller passes the company's active QR and transfer methods
    in their order. With none, the reminder still names the reference.

    `channel` only changes the closing line: in a chat the customer can "reply here", in an email
    a reply goes to a sender nobody reads, so the email asks them to let the company know.
    """
    lines = [_first_line(inv, today=today, brand=brand, locale=locale, tz=tz)]
    lines += [method_line(m, locale) for m in methods]
    lines.append(reference_line([_ref(inv)], locale))
    if locale == "ms":
        lines.append(
            "Kalau dah bayar, balas sini dan bagitahu bila ya."
            if channel == "chat"
            else "Kalau dah bayar, maklumkan kami bila ya."
        )
    else:
        lines.append(
            "If you've already paid, reply here and tell us when."
            if channel == "chat"
            else "If you've already paid, please let us know when."
        )
    return "\n".join(lines)
