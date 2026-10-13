"""Pure tests for staff callbacks, message formatting and quiet hours (no Docker)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from csa.channels.telegram import TelegramAdapter, TelegramUpdateError
from csa.contracts.callbacks import CallbackCodec, StaffCallbackCodec
from csa.notify.sender import format_notification, quiet_until

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
