"""What the subscription agent tells the reply composer: plans on offer and the customer's own.

Every fact states what the rows say, in words that read properly on their own, because when a
reply cannot be checked they are sent as they stand. Fact ids are constants (with an index where
there are several): never a subscription id, a plan code or anything of the contact's. A request
fact never says the thing is done: staff decide, and nothing changes until they do. A sign-up
fact never says it is approved or active: it is received, or registered and waiting to start.
A pending plan that waits for payment names the invoice it waits for, and one whose invoice the
customer says they paid says that is not confirmed yet; only one with no invoice says nothing has
been charged. A sign-up fact never says whether anything was charged.

A fact that can go out word for word (a write's: a sign-up turn, a pause, cancel or change request)
carries a casual Malay text (`Fact.text_ms`) beside the English when its builder is called with
`ms=True`; a read's facts stay English only, and the composer reads `text` alone. The Malay says
what the English says, with the same names, amounts, phone, email, reference and dates (Malay month
names, from `csa.payments.copy.local_date`). It is built after the English and apart from it: a
fault in the Malay leaves that fact English only (logged) and never touches the English or a
sign-up already filed.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from functools import partial
from typing import Any, Literal

import structlog

from csa.agents.facts import day, money
from csa.contracts.facts import Fact
from csa.payments.copy import local_date

logger = structlog.get_logger(__name__)

SUBSCRIPTION_SOURCE = "subscription_agent"
# `pending` waits for the status a later change adds; the live SQL allowlist already names it.
LIVE_STATUSES = ("active", "paused", "pending")
# A customer's request acts only on these (a plan waiting to be started cannot be changed yet).
REQUESTABLE_STATUSES = frozenset({"active", "paused"})

NONE_TEXT = "I can't find a subscription linked to this chat."
NONE_TEXT_MS = "Saya tak jumpa langganan yang berkait dengan chat ni."

RequestKind = Literal["passed", "which", "none", "already_ending", "already_paused", "waiting"]
# An opening invoice in these statuses is still waiting to be paid (`cash_pending`: in cash).
_WAITING_FOR_PAYMENT = frozenset({"issued", "overdue", "cash_pending"})
_VERBS = {"request_pause": "pause", "request_cancel": "cancel"}
_VERBS_MS = {"request_pause": "hentikan sementara", "request_cancel": "batalkan"}
# A plan's billing interval (`csa.subscriptions.contracts.Interval`) in a Malay price.
_PER_MS = {"month": "sebulan", "year": "setahun"}


def _malay(fact_id: str, build: Callable[[], str]) -> str | None:
    """The fact's Malay, or None when building it fails (logged): the English stands alone."""
    try:
        return build() or None
    except Exception as exc:
        logger.warning(
            "subscription_malay_failed",
            category="unexpected",
            fact=fact_id,
            error_type=type(exc).__name__,
        )
        return None


def _fact(fact_id: str, text: str, malay: Callable[[], str] | None = None) -> Fact:
    """A fact; with `malay`, its Malay text too (built apart, see `_malay`)."""
    text_ms = _malay(fact_id, malay) if malay is not None else None
    return Fact(id=fact_id, text=text, text_ms=text_ms, source=SUBSCRIPTION_SOURCE)


def _per(interval: object) -> str:
    return f" a {interval}" if interval else ""


def _per_ms(interval: object) -> str:
    return f" {_PER_MS.get(str(interval), str(interval))}" if interval else ""


def _plan_name(row: Mapping[str, Any]) -> str:
    return str(row.get("plan_name") or row["plan"])


def plan_facts(rows: Sequence[Mapping[str, Any]], *, ms: bool = False) -> list[Fact]:
    """One fact per plan on offer, with its price, interval and features.

    `ms`: the facts can go out word for word (a sign-up asking which plan), so each carries its
    Malay too; the features are the company's own words, as stored.
    """
    facts: list[Fact] = []
    for n, row in enumerate(rows, start=1):
        price = money(row["price"], row["currency"])
        interval = row.get("billing_interval")
        text = f"{row['name']}: {price}{_per(interval)}."
        raw = row.get("features") or []
        # A bare string is one feature, not a list of characters; a trailing '.' is dropped so
        # joining and the sentence's own '.' never print '..'.
        items = [raw] if isinstance(raw, str) else raw
        features = [t for t in (str(f).strip().rstrip(".").strip() for f in items) if t]
        if features:
            text += f" Includes: {'; '.join(features)}."
        malay = partial(_plan_ms, row["name"], price, interval, features) if ms else None
        facts.append(_fact(f"subscription_plan_{n}", text, malay))
    return facts


def _plan_ms(name: object, price: str, interval: object, features: Sequence[str]) -> str:
    """A plan fact's text in Malay."""
    listed = f" Termasuk: {'; '.join(features)}." if features else ""
    return f"{name}: {price}{_per_ms(interval)}.{listed}"


def _line(row: Mapping[str, Any], timezone: str) -> str | None:
    status = row["status"]
    name = _plan_name(row)
    price = f"{name}, {money(row['price'], row['currency'])}{_per(row.get('billing_interval'))}"
    renews: datetime | None = row.get("renews_at")
    if status == "cancelled":
        return f"Your plan: {name} was cancelled."
    if status == "paused":
        resume: date | None = row.get("resume_at")
        until = f" until {resume.strftime('%d %b %Y')}" if resume else ""
        text = f"Your plan: {name} is paused{until}."
    elif status == "pending":
        # The opening invoice (subscription_list.sql): open, with its reference, or none at all.
        ref = row.get("opening_invoice_reference")
        invoice = row.get("opening_invoice_status") if ref else None
        if invoice == "proof_submitted":
            text = (
                f"Your plan: {price}, starts when your payment is confirmed. You told us you "
                "paid; it isn't confirmed yet."
            )
        elif invoice in _WAITING_FOR_PAYMENT:
            text = (
                f"Your plan: {price}, starts when your first payment is confirmed. Invoice {ref} "
                "is waiting for payment."
            )
        else:
            text = (
                f"Your plan: {price}, is waiting to be started by the team. "
                "Nothing has been charged."
            )
    elif status == "active":
        text = f"Your plan: {price}."
        if row.get("cancel_at_period_end"):
            when = f" on {day(renews, timezone)}" if renews else ""
            text += f" It ends{when} and will not renew."
        elif renews:
            text += f" It renews on {day(renews, timezone)}."
    else:
        return None
    pending = row.get("pending_plan")
    if pending:
        new_name = row.get("pending_plan_name") or pending
        effective: datetime | None = row.get("pending_effective_at")
        if effective:
            text += f" It changes to {new_name} on {day(effective, timezone)}."
        else:
            text += f" It is set to change to {new_name}."
    return text


def subscription_facts(rows: Sequence[Mapping[str, Any]], timezone: str) -> list[Fact]:
    """One fact per subscription as the rows give them (newest first); none at all is said."""
    if not rows:
        return [_fact("subscription_none", NONE_TEXT)]
    facts: list[Fact] = []
    for row in rows:
        text = _line(row, timezone)
        if text is not None:
            facts.append(_fact(f"subscription_{len(facts) + 1}", text))
    return facts


def request_fact(
    kind: RequestKind,
    *,
    action: str | None = None,
    names: Sequence[str] = (),
    name: str | None = None,
    new_name: str | None = None,
    ends: datetime | None = None,
    timezone: str = "Asia/Kuala_Lumpur",
    names_ms: Callable[[], Sequence[str]] | None = None,
    ms: bool = False,
) -> Fact:
    """The answer to a pause, cancel or change request: passed on, or why it was not.

    `ms`: the fact can go out word for word (a request, not a read), so it carries its Malay too.
    `names_ms`: the Malay of `names` (a start date in Malay words), built with the Malay; without
    it the names are used as they are.
    """
    if kind == "passed":
        if action == "request_change":
            what = f"change {name} to {new_name}"
        else:
            what = f"{_VERBS.get(action or '', 'change')} {name}"
        text = f"Your request to {what} was passed to our staff to decide. Nothing has changed yet."
    elif kind == "which":
        if len(names) == 1:
            text = (
                "I can't find a plan by that name on this chat. "
                f"The plan I can see is {names[0]}. Is that the one you mean?"
            )
        else:
            text = f"This chat has more than one plan: {', '.join(names)}. Which one do you mean?"
    elif kind == "already_ending":
        when = f" on {day(ends, timezone)}" if ends else ""
        text = f"{name} is already set to end{when} and will not renew."
    elif kind == "already_paused":
        text = f"{name} is already paused."
    elif kind == "waiting":
        text = (
            f"{name} has not started yet, so it can't be paused, cancelled or changed yet. The "
            "team can help with any question about it."
        )
    else:
        text = NONE_TEXT

    def malay() -> str:
        return _request_ms(
            kind,
            action=action,
            names=list(names_ms() if names_ms is not None else names),
            name=name,
            new_name=new_name,
            ends=ends,
            timezone=timezone,
        )

    return _fact(f"subscription_{kind}", text, malay if ms else None)


def _request_ms(
    kind: RequestKind,
    *,
    action: str | None,
    names: Sequence[str],
    name: str | None,
    new_name: str | None,
    ends: datetime | None,
    timezone: str,
) -> str:
    """`request_fact`'s text in Malay."""
    if kind == "passed":
        if action == "request_change":
            what = f"tukar {name} ke {new_name}"
        else:
            what = f"{_VERBS_MS.get(action or '', 'tukar')} {name}"
        return (
            f"Permintaan awak nak {what} dah dihantar kat staf kami untuk diputuskan. Belum ada "
            "apa-apa yang berubah lagi."
        )
    if kind == "which":
        if len(names) == 1:
            return (
                "Saya tak jumpa pelan dengan nama tu dalam chat ni. "
                f"Pelan yang saya nampak ialah {names[0]}. Yang ni ke yang awak maksudkan?"
            )
        return (
            f"Chat ni ada lebih dari satu pelan: {', '.join(names)}. Yang mana satu awak maksudkan?"
        )
    if kind == "already_ending":
        when = f" pada {local_date(ends, 'ms', timezone)}" if ends else ""
        return f"{name} memang dah ditetapkan untuk tamat{when} dan takkan diperbaharui."
    if kind == "already_paused":
        return f"{name} memang dah dihentikan sementara."
    if kind == "waiting":
        return (
            f"{name} belum bermula lagi, jadi belum boleh dihentikan sementara, dibatalkan atau "
            "ditukar. Staf kami boleh tolong kalau awak ada apa-apa soalan pasal pelan ni."
        )
    return NONE_TEXT_MS


# Sign-up (request_signup). The composer rewrites these in the customer's language and register;
# they are also sent as they stand (a payment answer in the same turn), so each is said to the
# customer, never to the composer. They name the plan and, in the read-back, what the customer
# typed: never anything of another contact, a decline reason or an id.
SIGNUP_DATA_NOTE = (
    "We keep your name and phone number on your contact record so the team can set up your plan."
)
# Said after the data note only on a turn that asks for the email (WhatsApp): what the address is
# used for. It promises no receipts (none exist). Telegram never asks, so never gets it.
SIGNUP_EMAIL_NOTE = (
    " If you give us your email, we use it for payment reminders and booking messages."
)
# The same two in Malay, joined under the same condition (`with_email`); the same promise.
SIGNUP_DATA_NOTE_MS = (
    "Kami simpan nama dan nombor telefon awak dalam rekod pelanggan, supaya staf kami boleh "
    "sediakan pelan awak."
)
SIGNUP_EMAIL_NOTE_MS = (
    " Kalau awak bagi e-mel, kami guna untuk peringatan bayaran dan mesej tempahan."
)
# The read-back says this instead of the digits of a phone the channel verified.
VERIFIED_PHONE = "the number already on this chat"
VERIFIED_PHONE_MS = "nombor yang dah ada dalam chat ni"

SignupField = Literal["full name", "phone number"]
# What a sign-up still needs (`SignupField`), in Malay.
_FIELDS_MS = {"full name": "nama penuh", "phone number": "nombor telefon"}

SignupKind = Literal[
    "missing",
    "data_note",
    "readback",
    "phone_invalid",
    "email_ask",
    "email_invalid",
    "filed",
    "filed_auto",
    "filed_auto_invoice",
    "exists",
    "has_plan",
    "closed",
    "which",
    "try_later",
]


def signup_fact(
    kind: SignupKind,
    n: int = 1,
    *,
    plan: str | None = None,
    field: SignupField | None = None,
    name: str | None = None,
    phone: str | None = None,
    ref: str | None = None,
    payments: bool = False,
    email: str | None = None,
    with_email: bool = False,
    ms: bool = False,
) -> Fact:
    """One sign-up fact; its id is the kind and its place in the result (`signup_missing_1`).

    `payments`: the company's payments are on and the plan is priced, so an approved plan gets an
    invoice (`filed`). `ref`: the reference of the opening invoice an auto plan's approval issued
    (`filed_auto_invoice`). `with_email`: the email was asked (WhatsApp), so the read-back names
    `email`, the customer's own address, or says there is none, and the data note adds what an
    email is used for (`SIGNUP_EMAIL_NOTE`). `ms`: the fact carries its Malay too (a sign-up's
    facts can go out word for word).
    """
    given = (f", email {email}" if email else ", no email") if with_email else ""
    filed_next = (
        "The team will review it; once it is approved you'll get an invoice with how to pay, and "
        "the plan starts when the payment is confirmed."
        if payments
        else "The team will review it and tell you what happens next."
    )
    texts: dict[str, str] = {
        "missing": f"To sign up for {plan} I still need your {field}.",
        "data_note": SIGNUP_DATA_NOTE + (SIGNUP_EMAIL_NOTE if with_email else ""),
        "readback": (
            f"Sign-up for {plan}: name {name}, phone {phone}{given}. Is that right? Reply yes "
            "to sign up. Nothing is filed until you do."
        ),
        "phone_invalid": (
            "That phone number does not look right. Please type it again with the area or "
            "country code."
        ),
        "email_ask": (
            "If you'd like payment reminders by email, tell me your email address, or say skip. "
            "We keep it on your contact record to send you messages about your plan and bookings."
        ),
        "email_invalid": (
            "That email address does not look right. Please type it again, or say skip."
        ),
        "filed": f"Your sign-up for {plan} was received. {filed_next} Nothing is active yet.",
        "filed_auto": (
            f"Your sign-up for {plan} is registered and waiting to be started by the team."
        ),
        "filed_auto_invoice": (
            f"Your sign-up for {plan} is registered. Invoice {ref} is ready: ask me how to pay. "
            "The plan starts when the payment is confirmed."
        ),
        "exists": f"You already have a sign-up for {plan} waiting for the team.",
        "has_plan": f"You already have {plan}.",
        "closed": f"{plan} is not taking new sign-ups right now.",
        "which": "To sign up I still need to know which plan you want.",
        "try_later": (
            "The sign-up could not be filed just now, so nothing was filed. Please try again "
            "in a little while."
        ),
    }

    def malay() -> str:
        return _signup_ms(
            kind,
            plan=plan,
            field=field,
            name=name,
            phone=phone,
            ref=ref,
            payments=payments,
            email=email,
            with_email=with_email,
        )

    return _fact(f"signup_{kind}_{n}", texts[kind], malay if ms else None)


def _signup_ms(
    kind: SignupKind,
    *,
    plan: str | None,
    field: SignupField | None,
    name: str | None,
    phone: str | None,
    ref: str | None,
    payments: bool,
    email: str | None,
    with_email: bool,
) -> str:
    """`signup_fact`'s text in Malay."""
    given = (f", e-mel {email}" if email else ", tak ada e-mel") if with_email else ""
    shown = {VERIFIED_PHONE: VERIFIED_PHONE_MS}.get(phone or "", phone)
    filed_next = (
        "Staf kami akan semak dulu; lepas diluluskan, awak akan dapat invois dengan cara nak "
        "bayar, dan pelan bermula bila bayaran dah disahkan."
        if payments
        else "Staf kami akan semak dan bagitahu awak apa langkah seterusnya."
    )
    texts: dict[str, str] = {
        "missing": (
            f"Untuk daftar {plan}, saya masih perlukan {_FIELDS_MS.get(str(field), str(field))} "
            "awak."
        ),
        "data_note": SIGNUP_DATA_NOTE_MS + (SIGNUP_EMAIL_NOTE_MS if with_email else ""),
        "readback": (
            f"Pendaftaran {plan}: nama {name}, telefon {shown}{given}. Betul ke? Balas ya untuk "
            "daftar. Tak ada apa-apa yang didaftarkan selagi awak belum balas ya."
        ),
        "phone_invalid": (
            "Nombor telefon tu macam tak betul. Taip sekali lagi dengan kod kawasan atau kod "
            "negara ya."
        ),
        "email_ask": (
            "Kalau awak nak peringatan bayaran melalui e-mel, bagitahu alamat e-mel awak, atau "
            "cakap skip. Kami simpan dalam rekod pelanggan untuk hantar mesej pasal pelan dan "
            "tempahan awak."
        ),
        "email_invalid": "Alamat e-mel tu macam tak betul. Taip sekali lagi ya, atau cakap skip.",
        "filed": (
            f"Pendaftaran awak untuk {plan} dah diterima. {filed_next} Belum ada apa-apa yang "
            "aktif lagi."
        ),
        "filed_auto": (
            f"Pendaftaran awak untuk {plan} dah direkodkan dan tengah tunggu staf kami mulakan."
        ),
        "filed_auto_invoice": (
            f"Pendaftaran awak untuk {plan} dah direkodkan. Invois {ref} dah sedia: tanya saya "
            "cara nak bayar. Pelan bermula bila bayaran dah disahkan."
        ),
        "exists": f"Awak dah ada pendaftaran untuk {plan} yang tengah tunggu staf kami.",
        "has_plan": f"Awak dah ada {plan}.",
        "closed": f"{plan} tak buka pendaftaran baru buat masa ni.",
        "which": "Untuk daftar, saya masih perlu tahu pelan mana yang awak nak.",
        "try_later": (
            "Pendaftaran tak dapat dihantar tadi, jadi tak ada apa-apa yang didaftarkan. Cuba "
            "semula sekejap lagi ya."
        ),
    }
    return texts[kind]
