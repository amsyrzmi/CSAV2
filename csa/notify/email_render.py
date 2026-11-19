"""Notification emails: the words, as a subject, a plain-text part and a small HTML part.

Two renderers share one hard rule: an email never contains customer message text. It may name
the customer by display name and link to the console, nothing more.

- :func:`render_target_email` is for a company's staff email target. It says what the staff chat
  says (same headlines, same payload whitelist, :mod:`csa.notify.format`), in English or Malay.
- :func:`render_person_email` is for one person's own Inbox notification (the six
  ``inbox.*`` kinds), in the bell's English words.

Both read only whitelisted keys, escape every value in the HTML part, keep the subject to one
line of at most 200 characters, and never raise for a payload of the wrong shape. The caller
builds the outgoing mail with an address; this module imports nothing from ``csa.console``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from typing import Any

from csa.notify.format import (
    _HEADLINES,
    _MAX_NAME_CHARS,
    _PRIORITY_WORDS,
    _REPLY_APPROVAL_HEADLINE,
    INBOX_OVERDUE,
    _language,
    _overdue_lines,
)

MAX_SUBJECT_CHARS = 200
_BRAND = "FourStacks"
_UUID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.I)
# An id or a code word, never a sentence: no spaces, no punctuation beyond these.
_ID_SHAPED = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_BODY = ";".join(
    (
        "margin:0",
        "padding:24px",
        "font-family:Arial,Helvetica,sans-serif",
        "font-size:15px",
        "line-height:1.5",
        "color:#1f2933",
    )
)
_BUTTON = ";".join(
    (
        "display:inline-block",
        "padding:10px 18px",
        "background:#1f2933",
        "color:#ffffff",
        "text-decoration:none",
        "border-radius:6px",
    )
)

_TEST_HEADLINE = {"en": "Test email", "ms": "Emel ujian"}
_TEST_LINES = {
    "en": [
        "This is a test email from FourStacks.",
        "If you can read it, notification emails reach you.",
    ],
    "ms": [
        "Ini emel ujian daripada FourStacks.",
        "Jika anda dapat membacanya, emel pemberitahuan sampai kepada anda.",
    ],
}
_GENERIC_HEADLINE = {"en": "New notification", "ms": "Pemberitahuan baharu"}
_GENERIC_LINE = {
    "en": "Open the console to see the details.",
    "ms": "Buka konsol untuk melihat butirannya.",
}
_TARGET_WORDS = {
    "en": {
        "customer": "Customer",
        "priority": "Priority",
        "open_conversation": "Open conversation",
        "open_console": "Open the console",
        "fallback": "If the button does not work, copy this link into your browser:",
        "keys": {"order_id": "Order", "ticket_id": "Ticket", "agent": "Agent", "stage": "Stage"},
    },
    "ms": {
        "customer": "Pelanggan",
        "priority": "Keutamaan",
        "open_conversation": "Buka perbualan",
        "open_console": "Buka konsol",
        "fallback": "Jika butang tidak berfungsi, salin pautan ini ke pelayar anda:",
        "keys": {
            "order_id": "Pesanan",
            "ticket_id": "Tiket",
            "agent": "Ejen",
            "stage": "Peringkat",
        },
    },
}
# Ids and short labels only: never a key that can hold what a customer wrote (text, reason, ...).
_EXTRA_KEYS = ("order_id", "ticket_id", "agent", "stage")


@dataclass(frozen=True)
class RenderedEmail:
    """An email's subject, plain-text part and HTML part (no address: the caller adds it)."""

    subject: str
    text: str
    html: str


# --- shared helpers -------------------------------------------------------------------------------


def _one_line(value: str, limit: int | None = None) -> str:
    collapsed = " ".join(value.split())
    return collapsed[:limit].rstrip() if limit is not None else collapsed


def _subject(value: str) -> str:
    return _one_line(value, MAX_SUBJECT_CHARS) or _BRAND


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _name(value: object) -> str | None:
    """A display name on one line and of sane length, or None when missing or blank."""
    if not isinstance(value, str):
        return None
    return _one_line(value, _MAX_NAME_CHARS) or None


def _short(value: object) -> str | None:
    """An id-shaped string or whole number from the payload; anything else (free text) is None.

    The extra lines may carry ids and code words only. A value with a space or punctuation outside
    ``[A-Za-z0-9_.:-]``, or longer than 64 characters, could be a customer's sentence, so it is
    left out.
    """
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    text = str(value).strip()
    return text if _ID_SHAPED.fullmatch(text) else None


def _priority(value: object) -> str:
    """One of the four priority words, lower case, or "" for anything else."""
    level = _one_line(value).lower() if isinstance(value, str) else ""
    return level if level in _PRIORITY_WORDS["en"] else ""


def _base(base_url: object) -> str:
    return base_url.rstrip("/") if isinstance(base_url, str) else ""


def _conversation_url(base_url: str, conversation: object) -> str | None:
    if isinstance(conversation, str) and _UUID.fullmatch(conversation):
        # The portal path: tenant members are redirected away from /inbox, which drops the query.
        return f"{_base(base_url)}/portal/inbox?conversation={conversation}"
    return None


def _html(paragraphs: list[str], lang: str) -> str:
    body = "".join(f"<p>{paragraph}</p>\n" for paragraph in paragraphs)
    head = f'<!DOCTYPE html>\n<html lang="{lang}">\n<body style="{_BODY}">\n'
    return f"{head}{body}</body>\n</html>\n"


def _compose(
    lang: str,
    subject: str,
    headline: str,
    lines: list[str],
    link: str | None,
    action: str,
    fallback: str,
    footers: list[str],
) -> RenderedEmail:
    text_parts = [headline, *lines]
    html_parts = [f"<strong>{escape(headline)}</strong>", *(escape(line) for line in lines)]
    if link is not None:
        text_parts.append(f"{action}:\n{link}")
        href = escape(link)
        html_parts.append(f'<a href="{href}" style="{_BUTTON}">{escape(action)}</a>')
        html_parts.append(f"{escape(fallback)}<br>{href}")
    text_parts.extend([*footers, _BRAND])
    html_parts.extend([*(escape(f) for f in footers), _BRAND])
    return RenderedEmail(
        subject=_subject(subject),
        text="\n\n".join(text_parts) + "\n",
        html=_html(html_parts, lang),
    )


# --- staff email targets --------------------------------------------------------------------------


def render_target_email(
    event: str,
    priority: str,
    payload: dict[str, Any],
    language: str,
    base_url: str,
) -> RenderedEmail:
    """The email a staff email target gets for ``event``; never message text, never raises.

    An unknown language is English; an unknown event gets a generic notice that names no payload
    value. Customer name, wait, priority and repeat come from the payload's whitelisted keys only.
    """
    lang = _language(language)
    data = _mapping(payload)
    words = _TARGET_WORDS[lang]
    if event == "test":
        headline = _TEST_HEADLINE[lang]
        return _compose(lang, headline, headline, _TEST_LINES[lang], None, "", "", [])
    known = isinstance(event, str) and event in _HEADLINES["en"]
    if not known:
        headline = _GENERIC_HEADLINE[lang]
        link = f"{_base(base_url)}/"
        return _compose(
            lang,
            headline,
            headline,
            [_GENERIC_LINE[lang]],
            link,
            words["open_console"],
            words["fallback"],
            [],
        )
    headline = _HEADLINES[lang].get(event) or _HEADLINES["en"][event]
    if event == "approval_needed" and data.get("approval_id"):
        headline = _REPLY_APPROVAL_HEADLINE[lang]
    customer = _name(data.get("customer"))
    shown = _priority(priority)
    if event == INBOX_OVERDUE:
        # The payload's priority is printed by the shared overdue lines: only a known word gets in.
        known_level = _priority(data.get("priority"))
        lines = _overdue_lines(shown or "normal", {**data, "priority": known_level}, lang)
    else:
        lines = []
        if customer:
            lines.append(f"{words['customer']}: {customer}")
        if shown:
            lines.append(f"{words['priority']}: {_PRIORITY_WORDS[lang][shown]}")
        for key in _EXTRA_KEYS:
            value = _short(data.get(key))
            if value:
                lines.append(f"{words['keys'][key]}: {value}")
    url = _conversation_url(base_url, data.get("conversation_id"))
    link = url or f"{_base(base_url)}/"
    action = words["open_conversation"] if url else words["open_console"]
    subject = f"{headline}: {customer}" if customer else headline
    return _compose(lang, subject, headline, lines, link, action, words["fallback"], [])


def render_test_email(language: str) -> RenderedEmail:
    """The mail a target's "send a test" button sends: no link, no payload."""
    return render_target_email("test", "normal", {}, language, "")


# --- a person's own Inbox notifications -----------------------------------------------------------

_SETTINGS_NOTE = "You get this email because of your notification settings in the console."
_PERSON_TITLES = {
    "inbox.overdue": "Waiting for your team",
    "inbox.snooze_due": "Snooze ended",
    "inbox.stale_claim": "Claimed, no reply yet",
    "inbox.follow_up": "Follow up or resolve",
    "inbox.handback": "Hand it back?",
    "inbox.assigned": "Assigned to you",
}


def _plural(count: int, unit: str) -> str:
    return f"{count} {unit}{'' if count == 1 else 's'}"


def _bell_duration(minutes: object) -> str | None:
    """A wait as the bell writes it: minutes, hours and minutes, then whole days from 48 hours."""
    if isinstance(minutes, bool) or not isinstance(minutes, int | float):
        return None
    if isinstance(minutes, float) and not math.isfinite(minutes):
        return None
    whole = math.floor(minutes)
    if whole < 1:
        return None
    if whole < 60:
        return _plural(whole, "minute")
    if whole < 48 * 60:
        hours, rest = divmod(whole, 60)
        if rest == 0:
            return _plural(hours, "hour")
        return f"{_plural(hours, 'hour')} {_plural(rest, 'minute')}"
    return _plural(whole // (24 * 60), "day")


def _person_detail(kind: str, params: Mapping[str, Any]) -> str:
    who = _name(params.get("customer")) or "A customer"
    waited = _bell_duration(params.get("waited_minutes"))
    if kind == INBOX_OVERDUE:
        detail = f"{who} has waited {waited}." if waited else f"{who} is waiting for your team."
        level = params.get("priority")
        level = level.strip().lower() if isinstance(level, str) else ""
        if level in ("high", "urgent"):
            detail += f" Priority: {level}."
        repeat = params.get("repeat")
        if isinstance(repeat, int) and not isinstance(repeat, bool) and repeat > 1:
            detail += f" Reminder {repeat}."
        return detail
    if kind == "inbox.snooze_due":
        return f"{who} is back in Needs us."
    if kind == "inbox.stale_claim":
        extra = f" Waiting {waited}." if waited else ""
        return f"{who} was claimed but has had no reply.{extra}"
    if kind == "inbox.follow_up":
        since = f" for {waited}" if waited else ""
        return f"{who} has not answered{since}. Follow up or resolve it."
    if kind == "inbox.handback":
        since = f" for {waited}" if waited else ""
        return f"{who} has not answered{since}. You can hand the chat back to the assistant."
    return f"{who} was assigned to you."


def render_person_email(
    kind: str,
    params: dict[str, Any],
    link: dict[str, Any],
    base_url: str,
) -> RenderedEmail:
    """A person's email for one of their Inbox notifications, in English; never raises.

    The wording is the bell's. ``link`` is the notification's own: the conversation, the
    Notifications page, or (anything else) the console's front page.
    """
    data = _mapping(params)
    target = _mapping(link)
    known = isinstance(kind, str) and kind in _PERSON_TITLES
    title = _PERSON_TITLES[kind] if known else _GENERIC_HEADLINE["en"]
    detail = _person_detail(kind, data) if known else _GENERIC_LINE["en"]
    route = target.get("route")
    url = _conversation_url(base_url, target.get("conversation")) if route == "inbox" else None
    if url:
        action = "Open conversation"
    elif route == "notifications":
        url, action = f"{_base(base_url)}/portal/notifications", "Open notifications"
    else:
        url, action = f"{_base(base_url)}/", "Open the console"
    return _compose(
        "en", title, title, [detail], url, action, _TARGET_WORDS["en"]["fallback"], [_SETTINGS_NOTE]
    )
