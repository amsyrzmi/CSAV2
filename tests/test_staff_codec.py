"""Pure tests for staff callbacks, message formatting and quiet hours (no Docker)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from csa.channels.telegram import TelegramAdapter, TelegramUpdateError
from csa.contracts.callbacks import CallbackCodec, StaffCallbackCodec
from csa.notify import sender
from csa.notify.format import format_notification
from csa.notify.sender import quiet_until

QUIET = {"start": "22:00", "end": "07:00", "tz": "UTC"}


def test_staff_codec_round_trip_and_separation() -> None:
    action_id = uuid.uuid4()
    token = StaffCallbackCodec.encode(action_id)
    assert StaffCallbackCodec.decode(token) == action_id
    assert CallbackCodec.decode(token) is None
    assert StaffCallbackCodec.decode(CallbackCodec.encode(action_id)) is None
    assert StaffCallbackCodec.decode("s:not-a-uuid") is None


def _callback_update(data: str) -> dict[str, object]:
    return {
        "update_id": 5,
        "callback_query": {
            "id": "cq1",
            "from": {"id": 999, "first_name": "Staff"},
            "data": data,
            "message": {"chat": {"id": 999, "type": "private"}, "date": 1700000000},
        },
    }


def test_parse_staff_callback_sets_staff_action() -> None:
    action_id = uuid.uuid4()
    event = TelegramAdapter.parse(
        _callback_update(StaffCallbackCodec.encode(action_id)), public_id="p" * 16
    )
    assert event.staff_action_id == action_id
    assert event.callback is None
    assert event.sender_id == "999"


def test_parse_rejects_unknown_callback_data() -> None:
    with pytest.raises(TelegramUpdateError):
        TelegramAdapter.parse(_callback_update("x:1"), public_id="p" * 16)


def test_quiet_hours_overnight_window() -> None:
    inside = datetime(2025, 1, 1, 23, 30, tzinfo=UTC)
    assert quiet_until(QUIET, "high", inside) == datetime(2025, 1, 2, 7, 0, tzinfo=UTC)
    early = datetime(2025, 1, 1, 3, 0, tzinfo=UTC)
    assert quiet_until(QUIET, "normal", early) == datetime(2025, 1, 1, 7, 0, tzinfo=UTC)
    assert quiet_until(QUIET, "normal", datetime(2025, 1, 1, 12, 0, tzinfo=UTC)) is None
    assert quiet_until(QUIET, "urgent", inside) is None
    assert quiet_until(None, "high", inside) is None


def test_format_notification_is_bounded() -> None:
    text = format_notification("approval_needed", "high", {"order_id": "ORD-1", "text": "x" * 9000})
    assert text.startswith("[HIGH] Order needs approval")
    assert len(text) <= 4000


def test_format_notification_english_is_unchanged_for_existing_events() -> None:
    payload = {"conversation_id": "c1", "reason": "refund", "agent": "support", "ticket_id": "T-9"}
    expected = "[URGENT] Escalation\nticket_id: T-9\nreason: refund\nagent: support"
    assert format_notification("escalation", "urgent", payload) == expected
    assert format_notification("escalation", "urgent", payload, "en") == expected
    assert format_notification("dlq", "low", {}) == "[LOW] Dead-letter item"
    assert (
        format_notification("approval_needed", "high", {"approval_id": "a1"})
        == "[HIGH] AI reply needs approval"
    )
    assert format_notification("some.future_event", "normal", {}) == "[NORMAL] some.future_event"


def test_format_notification_malay_headlines() -> None:
    assert format_notification("escalation", "high", {"reason": "refund"}, "ms") == (
        "[HIGH] Eskalasi\nreason: refund"
    )
    assert format_notification("approval_needed", "high", {"order_id": "ORD-1"}, "ms") == (
        "[HIGH] Pesanan perlu kelulusan\norder_id: ORD-1"
    )
    assert format_notification("approval_needed", "high", {"approval_id": "a1"}, "ms") == (
        "[HIGH] Balasan AI perlu kelulusan"
    )
    for event, headline in (
        ("order_submitted", "Pesanan dihantar"),
        ("dlq", "Item gagal (dead-letter)"),
        ("circuit_opened", "Litar ejen dibuka"),
        ("booking_pending", "Tempahan menunggu pengesahan"),
    ):
        assert format_notification(event, "normal", {}, "ms") == f"[NORMAL] {headline}"


def test_format_notification_unknown_language_or_event_falls_back_and_never_raises() -> None:
    english = format_notification("escalation", "high", {"reason": "refund"})
    assert format_notification("escalation", "high", {"reason": "refund"}, "fr") == english
    assert format_notification("escalation", "high", {"reason": "refund"}, "") == english
    assert format_notification("some.future_event", "high", {}, "ms") == "[HIGH] some.future_event"
    overdue = format_notification("inbox.overdue", "high", {"customer": "Aini"}, "fr")
    assert overdue == format_notification("inbox.overdue", "high", {"customer": "Aini"}, "en")
    for payload in ({}, {"waited_minutes": "soon", "repeat": None, "customer": 7}):
        for language in ("en", "ms", "xx"):
            assert format_notification("inbox.overdue", "normal", payload, language)


def test_overdue_message_in_english() -> None:
    payload = {
        "customer": "Aini",
        "waited_minutes": 65,
        "repeat": 1,
        "conversation_id": "c1",
        "priority": "high",
    }
    assert format_notification("inbox.overdue", "high", payload) == (
        "[HIGH] Customer waiting for staff\nAini has waited 1 hour 5 minutes.\nPriority: high."
    )
    again = format_notification(
        "inbox.overdue", "high", {**payload, "waited_minutes": 45, "repeat": 3}
    )
    assert again == (
        "[HIGH] Customer waiting for staff\nAini has waited 45 minutes.\nPriority: high.\n"
        "Reminder 3."
    )
    nobody = format_notification(
        "inbox.overdue", "normal", {"waited_minutes": 120, "customer": None}
    )
    assert nobody == (
        "[NORMAL] Customer waiting for staff\na customer has waited 2 hours.\nPriority: normal."
    )
    one = format_notification("inbox.overdue", "low", {"customer": "Aini", "waited_minutes": 1})
    assert "has waited 1 minute." in one


def test_overdue_message_in_malay() -> None:
    payload = {"customer": "Aini", "waited_minutes": 65, "repeat": 2, "priority": "urgent"}
    assert format_notification("inbox.overdue", "urgent", payload, "ms") == (
        "[URGENT] Pelanggan menunggu kakitangan\nAini sudah menunggu 1 jam 5 minit.\n"
        "Keutamaan: segera.\nPeringatan 2."
    )
    nobody = format_notification("inbox.overdue", "normal", {"waited_minutes": 30}, "ms")
    assert nobody == (
        "[NORMAL] Pelanggan menunggu kakitangan\nseorang pelanggan sudah menunggu 30 minit.\n"
        "Keutamaan: biasa."
    )


def test_overdue_message_never_carries_customer_message_text() -> None:
    secret = "my card number is 4111 1111 1111 1111"
    payload = {
        "customer": "Aini",
        "waited_minutes": 10,
        "text": secret,
        "message": secret,
        "body": secret,
        "reason": secret,
    }
    for language in ("en", "ms"):
        text = format_notification("inbox.overdue", "normal", payload, language)
        assert "4111" not in text and "card" not in text
        for key in ("text:", "message:", "body:", "reason:"):
            assert key not in text


def test_overdue_customer_name_is_one_bounded_line() -> None:
    text = format_notification(
        "inbox.overdue", "normal", {"customer": "Aini\nPriority: urgent " + "x" * 500}
    )
    assert len(text.splitlines()) == 3
    assert len(text) < 400


def test_sender_still_exports_the_formatter() -> None:
    from csa.notify import format as notify_format

    assert sender.format_notification is notify_format.format_notification
