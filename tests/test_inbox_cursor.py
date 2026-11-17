"""The Inbox queue list's opaque keyset cursor (csa/console/inbox_cursor.py): pure, no database."""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from csa.console.inbox_cursor import QueueCursor, QueueOrder, decode, encode

AT = datetime(2026, 11, 16, 9, 30, 15, 123456, tzinfo=UTC)
ID = UUID("0b9c6a52-6a3e-4d4f-9a51-2f0f8d6f1c11")


def _raw(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


@pytest.mark.parametrize("order", ["activity", "waiting", "due"])
def test_a_time_cursor_round_trips_exactly(order: QueueOrder) -> None:
    token = encode(order, at=AT, id=ID)
    assert decode(token, order) == QueueCursor(order=order, at=AT, id=ID)


def test_another_time_zone_comes_back_as_the_same_instant() -> None:
    local = AT.astimezone(timezone(timedelta(hours=8)))
    assert decode(encode("due", at=local, id=ID), "due").at == AT


@pytest.mark.parametrize("order", ["waiting", "due"])
def test_a_missing_sort_time_round_trips_as_none(order: QueueOrder) -> None:
    token = encode(order, at=None, id=ID)
    assert decode(token, order) == QueueCursor(order=order, at=None, id=ID)


@pytest.mark.parametrize("due", [AT, None])
@pytest.mark.parametrize("rank", [1, 2, 3, 4])
def test_a_priority_cursor_carries_rank_due_and_id(rank: int, due: datetime | None) -> None:
    token = encode("priority", at=due, id=ID, rank=rank)
    assert decode(token, "priority") == QueueCursor(order="priority", at=due, id=ID, rank=rank)


def test_the_token_is_url_safe_and_opaque() -> None:
    for _ in range(20):
        token = encode("priority", at=AT, id=uuid4(), rank=4)
        assert re.fullmatch(r"[A-Za-z0-9_-]+", token), token
        assert str(ID) not in token


def test_a_bare_conversation_id_is_a_legacy_activity_cursor() -> None:
    assert decode(str(ID), "activity") == QueueCursor(order="activity", at=None, id=ID, legacy=True)
    assert decode(str(ID).upper(), "activity").id == ID


@pytest.mark.parametrize("order", ["waiting", "due", "priority"])
def test_a_bare_conversation_id_pages_only_the_activity_order(order: QueueOrder) -> None:
    with pytest.raises(ValueError):
        decode(str(ID), order)


def test_a_cursor_of_another_order_is_refused() -> None:
    with pytest.raises(ValueError):
        decode(encode("due", at=AT, id=ID), "activity")
    with pytest.raises(ValueError):
        decode(encode("activity", at=AT, id=ID), "waiting")


def test_the_activity_cursor_needs_a_time_and_only_priority_takes_a_rank() -> None:
    with pytest.raises(ValueError):
        encode("activity", at=None, id=ID)
    with pytest.raises(ValueError):
        encode("due", at=AT, id=ID, rank=2)
    with pytest.raises(ValueError):
        encode("priority", at=AT, id=ID)
    with pytest.raises(ValueError):
        encode("priority", at=AT, id=ID, rank=5)
    with pytest.raises(ValueError):
        encode("due", at=AT.replace(tzinfo=None), id=ID)


@pytest.mark.parametrize(
    "token",
    [
        "",
        "!!!!",
        "not a cursor",
        "YWN0aXZpdHk=",  # padded
        _raw("activity"),
        _raw(f"activity|{AT.isoformat()}"),
        _raw(f"activity||{ID}"),
        _raw(f"activity|{AT.isoformat()}|not-a-uuid"),
        _raw(f"activity|yesterday|{ID}"),
        _raw(f"activity|{AT.replace(tzinfo=None).isoformat()}|{ID}"),
        _raw(f"activity|{AT.isoformat()}|{ID}|extra"),
        _raw(f"sideways|{AT.isoformat()}|{ID}"),
        _raw(f"priority|{AT.isoformat()}|{ID}"),
        _raw(f"priority|9|{AT.isoformat()}|{ID}"),
        _raw(f"priority|x|{AT.isoformat()}|{ID}"),
        _raw(f"priority|04|{AT.isoformat()}|{ID}"),
        "é" * 8,
        "A" * 300,
        f"{ID} ",
    ],
)
def test_a_malformed_cursor_is_a_value_error(token: str) -> None:
    order: QueueOrder = "priority" if "priority" in base64_text(token) else "activity"
    with pytest.raises(ValueError):
        decode(token, order)


def base64_text(token: str) -> str:
    try:
        return base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except Exception:
        return ""
