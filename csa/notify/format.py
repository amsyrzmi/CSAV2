"""Staff-chat message text, language aware (plan 2a, task 4).

Like :mod:`csa.notify.people` it imports :mod:`csa.notify.notifier` and :mod:`csa.core` only
(``pyproject.toml`` contract), so it stays light for every importer; in fact it needs nothing
but the standard library. :func:`format_notification` never raises and never prints customer
message text for the Inbox events: those name the customer by display name only.
"""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_LANGUAGE = "en"
LANGUAGES = ("en", "ms")
MAX_MESSAGE_CHARS = 4000
_MAX_NAME_CHARS = 80

# Events whose text is built from the payload's ids and counts only (no free text key is read).
INBOX_OVERDUE = "inbox.overdue"

_HEADLINES: dict[str, dict[str, str]] = {
    "en": {
        "escalation": "Escalation",
        "approval_needed": "Order needs approval",
        "order_submitted": "Order submitted",
        "dlq": "Dead-letter item",
        "circuit_opened": "Agent circuit opened",
        "booking_pending": "Booking waiting for confirmation",
        INBOX_OVERDUE: "Customer waiting for staff",
    },
    "ms": {
        "escalation": "Eskalasi",
        "approval_needed": "Pesanan perlu kelulusan",
        "order_submitted": "Pesanan dihantar",
        "dlq": "Item gagal (dead-letter)",
        "circuit_opened": "Litar ejen dibuka",
        "booking_pending": "Tempahan menunggu pengesahan",
        INBOX_OVERDUE: "Pelanggan menunggu kakitangan",
    },
}
_REPLY_APPROVAL_HEADLINE = {
    "en": "AI reply needs approval",
    "ms": "Balasan AI perlu kelulusan",
}

_PRIORITY_WORDS = {
    "en": {"low": "low", "normal": "normal", "high": "high", "urgent": "urgent"},
    "ms": {"low": "rendah", "normal": "biasa", "high": "tinggi", "urgent": "segera"},
}
# Words for the overdue line. {customer}, {waited}, {priority} and {repeat} are filled in.
_OVERDUE_TEXT: dict[str, dict[str, str]] = {
    "en": {
        "someone": "a customer",
        "waited": "{customer} has waited {waited}.",
        "no_wait": "{customer} is waiting.",
        "priority": "Priority: {priority}.",
        "repeat": "Reminder {repeat}.",
    },
    "ms": {
        "someone": "seorang pelanggan",
        "waited": "{customer} sudah menunggu {waited}.",
        "no_wait": "{customer} sedang menunggu.",
        "priority": "Keutamaan: {priority}.",
        "repeat": "Peringatan {repeat}.",
    },
}
# (singular, plural) of the units in a waited duration.
_UNITS = {
    "en": {"minute": ("minute", "minutes"), "hour": ("hour", "hours")},
    "ms": {"minute": ("minit", "minit"), "hour": ("jam", "jam")},
}

_EXTRA_KEYS = ("text", "order_id", "ticket_id", "reason", "agent", "stage", "trace_id")


def _language(language: str) -> str:
    return language if language in LANGUAGES else DEFAULT_LANGUAGE


def _whole_number(value: object) -> int | None:
    """A payload number as an int, or None when it is missing or not a number."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == value and abs(value) < 10**9 else None
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _duration(minutes: int, words: Mapping[str, tuple[str, str]]) -> str:
    """Minutes as ``45 minutes`` or ``1 hour 5 minutes`` (Malay ``45 minit``, ``1 jam 5 minit``)."""
    minutes = max(minutes, 0)
    hours, rest = divmod(minutes, 60)

    def part(count: int, unit: tuple[str, str]) -> str:
        return f"{count} {unit[0] if count == 1 else unit[1]}"

    if hours == 0:
        return part(rest, words["minute"])
    if rest == 0:
        return part(hours, words["hour"])
    return f"{part(hours, words['hour'])} {part(rest, words['minute'])}"


def _customer_name(value: object, fallback: str) -> str:
    """A display name on one line and of sane length, else the fallback."""
    if not isinstance(value, str):
        return fallback
    name = " ".join(value.split())[:_MAX_NAME_CHARS]
    return name or fallback


def _overdue_lines(priority: str, payload: Mapping[str, object], language: str) -> list[str]:
    text = _OVERDUE_TEXT[language]
    customer = _customer_name(payload.get("customer"), text["someone"])
    waited = _whole_number(payload.get("waited_minutes"))
    lines = [
        text["waited"].format(customer=customer, waited=_duration(waited, _UNITS[language]))
        if waited is not None
        else text["no_wait"].format(customer=customer)
    ]
    shown = payload.get("priority")
    level = str(shown if isinstance(shown, str) and shown else priority).lower()
    lines.append(text["priority"].format(priority=_PRIORITY_WORDS[language].get(level, level)))
    repeat = _whole_number(payload.get("repeat"))
    if repeat is not None and repeat > 1:
        lines.append(text["repeat"].format(repeat=repeat))
    return lines


def format_notification(
    event: str,
    priority: str,
    payload: Mapping[str, object],
    language: str = DEFAULT_LANGUAGE,
) -> str:
    """Render a short staff message; never includes raw customer text for Inbox events.

    An unknown language falls back to English, an unknown event to its own name; never raises.
    """
    lang = _language(language)
    headline = _HEADLINES[lang].get(event) or _HEADLINES[DEFAULT_LANGUAGE].get(event, event)
    if event == "approval_needed" and payload.get("approval_id"):
        headline = _REPLY_APPROVAL_HEADLINE[lang]
    lines = [f"[{priority.upper()}] {headline}"]
    if event == INBOX_OVERDUE:
        lines.extend(_overdue_lines(priority, payload, lang))
    else:
        for key in _EXTRA_KEYS:
            value = payload.get(key)
            if value:
                lines.append(f"{key}: {value}")
    return "\n".join(lines)[:MAX_MESSAGE_CHARS]
