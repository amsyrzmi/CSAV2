"""What the subscription agent tells the reply composer: plans on offer and the customer's own.

Every fact states what the rows say, in words that read properly on their own, because when a
reply cannot be checked they are sent as they stand. Fact ids are constants (with an index where
there are several): never a subscription id, a plan code or anything of the contact's. A request
fact never says the thing is done: staff decide, and nothing changes until they do. A sign-up
fact never says it is approved or active: it is received, or registered and waiting to start.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, Literal

from csa.agents.facts import day, money
from csa.contracts.facts import Fact

SUBSCRIPTION_SOURCE = "subscription_agent"
# `pending` waits for the status a later change adds; the live SQL allowlist already names it.
LIVE_STATUSES = ("active", "paused", "pending")
# A customer's request acts only on these (a plan waiting to be started cannot be changed yet).
REQUESTABLE_STATUSES = frozenset({"active", "paused"})

NONE_TEXT = "I can't find a subscription linked to this chat."

RequestKind = Literal["passed", "which", "none", "already_ending", "already_paused", "waiting"]
_VERBS = {"request_pause": "pause", "request_cancel": "cancel"}


def _fact(fact_id: str, text: str) -> Fact:
    return Fact(id=fact_id, text=text, source=SUBSCRIPTION_SOURCE)


def _per(interval: object) -> str:
    return f" a {interval}" if interval else ""


def _plan_name(row: Mapping[str, Any]) -> str:
    return str(row.get("plan_name") or row["plan"])


def plan_facts(rows: Sequence[Mapping[str, Any]]) -> list[Fact]:
    """One fact per plan on offer, with its price, interval and features."""
    facts: list[Fact] = []
    for n, row in enumerate(rows, start=1):
        price = money(row["price"], row["currency"])
        text = f"{row['name']}: {price}{_per(row.get('billing_interval'))}."
        raw = row.get("features") or []
        # A bare string is one feature, not a list of characters; a trailing '.' is dropped so
        # joining and the sentence's own '.' never print '..'.
        items = [raw] if isinstance(raw, str) else raw
        features = [t for t in (str(f).strip().rstrip(".").strip() for f in items) if t]
        if features:
            text += f" Includes: {'; '.join(features)}."
        facts.append(_fact(f"subscription_plan_{n}", text))
    return facts


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
        text = (
            f"Your plan: {price}, is waiting to be started by the team. Nothing has been charged."
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
) -> Fact:
    """The answer to a pause, cancel or change request: passed on, or why it was not."""
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
            f"{name} is waiting to be started by the team and nothing has been charged, so it "
            "can't be paused, cancelled or changed yet. The team can help with any question "
            "about it."
        )
    else:
        text = NONE_TEXT
    return _fact(f"subscription_{kind}", text)


# Sign-up (request_signup). The composer rewrites these in the customer's language and register;
# they still read properly when sent as they stand. They name the plan and, in the read-back, what
# the customer typed: never anything of another contact, a decline reason or an id.
SIGNUP_DATA_NOTE = (
    "We keep your name and phone number on your contact record so the team can set up your plan."
)
# The read-back says this instead of the digits of a phone the channel verified.
VERIFIED_PHONE = "the number already on this chat"

SignupKind = Literal[
    "missing",
    "data_note",
    "readback",
    "phone_invalid",
    "filed",
    "filed_auto",
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
    field: Literal["full name", "phone number"] | None = None,
    name: str | None = None,
    phone: str | None = None,
) -> Fact:
    """One sign-up fact; its id is the kind and its place in the result (`signup_missing_1`)."""
    texts: dict[str, str] = {
        "missing": f"To sign up for {plan} I still need your {field}.",
        "data_note": SIGNUP_DATA_NOTE,
        "readback": (
            f"Sign-up for {plan}: name {name}, phone {phone}. Ask the customer to confirm, and "
            "file nothing yet."
        ),
        "phone_invalid": (
            "That phone number does not look right. Ask for it again with the area or country code."
        ),
        "filed": (
            f"Your sign-up for {plan} was received. The team will review it and tell you what "
            "happens next. Nothing is active yet and nothing has been charged."
        ),
        "filed_auto": (
            f"Your sign-up for {plan} is registered and waiting to be started. Nothing has been "
            "charged."
        ),
        "exists": f"You already have a sign-up for {plan} waiting for the team.",
        "has_plan": f"You already have {plan}.",
        "closed": f"{plan} is not taking new sign-ups right now.",
        "which": "To sign up I still need to know which plan you want.",
        "try_later": (
            "The sign-up could not be filed just now, so nothing was filed. Ask the customer to "
            "try again in a little while."
        ),
    }
    return _fact(f"signup_{kind}_{n}", texts[kind])
