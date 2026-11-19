"""csa.notify.email_render: the notification emails never carry message text and never raise."""

from __future__ import annotations

from typing import Any

import pytest
from csa.notify.email_render import (
    RenderedEmail,
    render_person_email,
    render_target_email,
    render_test_email,
)

BASE = "https://console.example"
CONVERSATION = "7f0c2a64-1b3e-4d55-9a0e-2c5d8e1f4b73"
SENTINEL = "SENTINEL-do-not-leak-4471"
TARGET_EVENTS = (
    "escalation",
    "approval_needed",
    "order_submitted",
    "dlq",
    "circuit_opened",
    "booking_pending",
    "inbox.overdue",
    "test",
)
PERSON_KINDS = (
    "inbox.assigned",
    "inbox.overdue",
    "inbox.snooze_due",
    "inbox.stale_claim",
    "inbox.follow_up",
    "inbox.handback",
)
LEAKY = {
    "text": SENTINEL,
    "message": SENTINEL,
    "body": SENTINEL,
    "last_message": SENTINEL,
    "reason": SENTINEL,
}


def _everything(mail: RenderedEmail) -> str:
    return "\n".join((mail.subject, mail.text, mail.html))


@pytest.mark.parametrize("language", ["en", "ms"])
@pytest.mark.parametrize("event", [*TARGET_EVENTS, "something.new"])
def test_no_message_text_for_any_event(event: str, language: str) -> None:
    payload = {**LEAKY, "customer": "Aisha", "conversation_id": CONVERSATION}
    mail = render_target_email(event, "high", payload, language, BASE)
    assert SENTINEL not in _everything(mail)


@pytest.mark.parametrize("kind", [*PERSON_KINDS, "something.new"])
def test_no_message_text_for_any_kind(kind: str) -> None:
    params = {**LEAKY, "customer": "Aisha", "conversation_id": CONVERSATION}
    link = {"route": "inbox", "conversation": CONVERSATION, **LEAKY}
    mail = render_person_email(kind, params, link, BASE)
    assert SENTINEL not in _everything(mail)


@pytest.mark.parametrize("base", [BASE, BASE + "/"])
def test_links_use_base_url(base: str) -> None:
    want = f"{BASE}/inbox?conversation={CONVERSATION}"
    link = {"route": "inbox", "conversation": CONVERSATION}
    person = render_person_email("inbox.overdue", {"customer": "Aisha"}, link, base)
    assert want in person.text and want in person.html
    target = render_target_email(
        "inbox.overdue", "high", {"conversation_id": CONVERSATION}, "en", base
    )
    assert want in target.text and want in target.html
    assert "//inbox" not in _everything(person) + _everything(target)
    page = render_person_email("inbox.assigned", {}, {"route": "notifications"}, base)
    assert f"{BASE}/portal/notifications" in page.text
    odd = render_person_email("inbox.assigned", {}, {"route": "elsewhere"}, base)
    assert f"{BASE}/\n" in odd.text or odd.text.rstrip().endswith(f"{BASE}/")


def test_conversation_id_must_look_like_an_id() -> None:
    link = {"route": "inbox", "conversation": 'x"><script>alert(1)</script>'}
    mail = render_person_email("inbox.assigned", {"customer": "A"}, link, BASE)
    assert "<script>" not in mail.html
    assert "conversation=" not in _everything(mail)


def test_malay_target_email() -> None:
    payload = {"customer": "Aisha", "waited_minutes": 45, "conversation_id": CONVERSATION}
    en = render_target_email("inbox.overdue", "high", payload, "en", BASE)
    ms = render_target_email("inbox.overdue", "high", payload, "ms", BASE)
    assert en != ms
    assert en.subject != ms.subject
    assert "Aisha" in _everything(en) and "Aisha" in _everything(ms)
    assert "Pelanggan menunggu kakitangan" in ms.subject
    assert "45 minit" in ms.text
    assert "45 minutes" in en.text
    assert 'lang="ms"' in ms.html and 'lang="en"' in en.html
    unknown = render_target_email("inbox.overdue", "high", payload, "fr", BASE)
    assert unknown == en


@pytest.mark.parametrize(
    "name", ["A\r\nBcc: x@y", "<script>alert(1)</script>", "N" * 2000, " \n\t "]
)
@pytest.mark.parametrize("event", ["escalation", "inbox.overdue"])
def test_hostile_display_name(name: str, event: str) -> None:
    mail = render_target_email(event, "urgent", {"customer": name}, "en", BASE)
    assert "\r" not in mail.subject and "\n" not in mail.subject
    assert len(mail.subject) <= 200
    assert "<script>" not in mail.html
    if name.startswith("<script>"):
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in mail.html
    person = render_person_email("inbox.assigned", {"customer": name}, {}, BASE)
    assert "\r" not in person.subject and "\n" not in person.subject
    assert len(person.subject) <= 200
    assert "<script>" not in person.html


def test_unknown_event_gets_generic_mail() -> None:
    payload = {**LEAKY, "customer": "Aisha", "internal": SENTINEL, "conversation_id": CONVERSATION}
    mail = render_target_email("something.new", "normal", payload, "en", BASE)
    assert mail.subject and mail.text and mail.html
    assert SENTINEL not in _everything(mail)
    assert "something.new" not in _everything(mail)


@pytest.mark.parametrize("kind", PERSON_KINDS)
def test_all_kinds_render(kind: str) -> None:
    params = {"customer": "Aisha", "waited_minutes": 90, "priority": "urgent", "repeat": 2}
    link = {"route": "inbox", "conversation": CONVERSATION}
    mail = render_person_email(kind, params, link, BASE)
    assert mail.subject and "Aisha" in mail.text and "Aisha" in mail.html
    assert f"{BASE}/inbox?conversation={CONVERSATION}" in mail.html
    assert "\n" not in mail.subject
    assert kind not in _everything(mail)


def test_person_wording_matches_the_bell() -> None:
    link = {"route": "inbox", "conversation": CONVERSATION}
    params = {"customer": "Aisha", "waited_minutes": 125, "priority": "urgent", "repeat": 3}
    overdue = render_person_email("inbox.overdue", params, link, BASE)
    assert overdue.subject == "Waiting for your team"
    assert "Aisha has waited 2 hours 5 minutes. Priority: urgent. Reminder 3." in overdue.text
    low = render_person_email("inbox.overdue", {**params, "priority": "low"}, link, BASE)
    assert "Priority" not in low.text
    bare = render_person_email("inbox.overdue", {}, link, BASE)
    assert "A customer is waiting for your team." in bare.text
    days = render_person_email("inbox.follow_up", {"waited_minutes": 3000}, link, BASE)
    assert "A customer has not answered for 2 days. Follow up or resolve it." in days.text
    assigned = render_person_email("inbox.assigned", {"customer": "Aisha"}, link, BASE)
    assert assigned.subject == "Assigned to you"
    assert "Aisha was assigned to you." in assigned.text


def test_target_email_content() -> None:
    payload = {
        "customer": "Aisha",
        "waited_minutes": 70,
        "priority": "urgent",
        "repeat": 2,
        "order_id": "ORD-9",
        "conversation_id": CONVERSATION,
    }
    overdue = render_target_email("inbox.overdue", "high", payload, "en", BASE)
    assert "Customer waiting for staff" in overdue.subject
    assert "Aisha has waited 1 hour 10 minutes." in overdue.text
    assert "Priority: urgent." in overdue.text and "Reminder 2." in overdue.text
    order = render_target_email("order_submitted", "normal", payload, "en", BASE)
    assert "Order submitted" in order.subject and "ORD-9" in order.text
    assert "Aisha" in order.text
    approval = render_target_email("approval_needed", "high", {"approval_id": "a1"}, "en", BASE)
    assert "AI reply needs approval" in approval.subject


def test_test_email() -> None:
    en, ms = render_test_email("en"), render_test_email("ms")
    assert en != ms and en.subject and ms.subject
    assert en.text and en.html and "<html" in en.html
    assert render_test_email("fr") == en
    via_target = render_target_email("test", "normal", {}, "en", BASE)
    assert via_target.subject == en.subject


ODD_PAYLOADS: list[Any] = [
    {},
    {"customer": None, "waited_minutes": "abc", "repeat": [], "priority": 7},
    {"customer": 12, "waited_minutes": float("nan"), "conversation_id": 5},
    {"waited_minutes": -5, "repeat": True, "order_id": {"a": 1}, "agent": ["x"]},
    {"waited_minutes": 10**30, "customer": {"a": 1}},
]


@pytest.mark.parametrize("payload", ODD_PAYLOADS)
def test_never_raises_for_odd_payloads(payload: dict[str, Any]) -> None:
    for event in [*TARGET_EVENTS, "x"]:
        for language in ("en", "ms", "??"):
            mail = render_target_email(event, "high", payload, language, BASE)
            assert mail.subject and mail.text and mail.html
            assert "None" not in mail.text and "nan" not in mail.text.lower().split()
    for kind in [*PERSON_KINDS, "x"]:
        mail = render_person_email(kind, payload, {"route": 5, "conversation": None}, BASE)
        assert mail.subject and mail.text and mail.html
        assert "undefined" not in mail.text and "None" not in mail.text
    mail = render_person_email("inbox.overdue", payload, None, BASE)  # type: ignore[arg-type]
    assert mail.subject
    mail = render_target_email("escalation", "high", None, "en", BASE)  # type: ignore[arg-type]
    assert mail.subject
