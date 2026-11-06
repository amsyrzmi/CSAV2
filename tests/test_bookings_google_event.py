"""What a Google event holds, what a booking calls for, and the rule the sync rests on: every change
to a booking raises its version. No database."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from csa.bookings import service
from csa.bookings.contracts import BookingCustomerOut, BookingGuestOut
from csa.bookings.google.push import event_body, plan_for

from tests.conftest import REPO_ROOT

ZONE = ZoneInfo("Asia/Kuala_Lumpur")
AINA = uuid.uuid4()


def record(**over: Any) -> service.BookingRecord:
    base = service.BookingRecord(
        id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        status="confirmed",
        kind="appointment",
        title="Haircut",
        private=False,
        starts_at=datetime(2031, 3, 4, 2, tzinfo=UTC),
        ends_at=datetime(2031, 3, 4, 2, 30, tzinfo=UTC),
        version=3,
        type_id=None,
        created_by=None,
        location_kind="in_person",
        address="12 Jalan Contoh, Kuala Lumpur",
        meeting_link=None,
        customer=BookingCustomerOut(
            id=uuid.uuid4(),
            full_name="Siti Customer",
            phone="+60123456789",
            email="siti@example.test",
            given_email="siti.chat@example.test",
        ),
        people=(service.Attendee(AINA, "Aina Salleh", True, None, "aina@example.test", None),),
        resources=(),
        guests=(
            BookingGuestOut(name="Guest Name", email="guest@example.test"),
            BookingGuestOut(name=None, email="nameless@example.test"),
        ),
        conflict=False,
    )
    return replace(base, **over)


# --- what an event holds ---------------------------------------------------------------------


@pytest.mark.parametrize("over", [{}, {"private": True}, {"source": "bot"}])
def test_an_event_holds_the_customers_name_and_nothing_else_about_them(
    over: dict[str, Any],
) -> None:
    body = event_body(record(**over), zone=ZONE, brand="Synthetic Co")
    text = json.dumps(body)
    assert body["summary"] == "Haircut"
    assert body["start"] == {
        "dateTime": "2031-03-04T10:00:00+08:00",
        "timeZone": "Asia/Kuala_Lumpur",
    }
    assert body["end"]["dateTime"] == "2031-03-04T10:30:00+08:00"
    assert body["location"] == "12 Jalan Contoh, Kuala Lumpur"
    assert "Customer: Siti Customer" in body["description"]
    assert "With: Aina Salleh, Guest Name" in body["description"]
    assert "Google Maps:" in body["description"] and "Waze:" in body["description"]
    for secret in ("+60123456789", "siti@", "siti.chat@", "guest@", "nameless@", "aina@"):
        assert secret not in text
    assert "attendees" not in body


def test_an_online_booking_shows_its_link_and_a_phone_one_says_so() -> None:
    link = "https://meet.example.test/abc"
    online = event_body(
        record(location_kind="online", address=None, meeting_link=link), zone=ZONE, brand="B"
    )
    assert online["location"] == link and f"Join: {link}" in online["description"]
    phone = event_body(record(location_kind="phone", address=None), zone=ZONE, brand="B")
    assert phone["location"] == "By phone"
    bare = event_body(record(location_kind=None, address=None, title=None), zone=ZONE, brand="B")
    assert "location" not in bare and bare["summary"] == "Booking"


@pytest.mark.parametrize(
    ("over", "plan"),
    [
        ({}, "upsert"),
        (
            {
                "starts_at": datetime(2001, 1, 1, tzinfo=UTC),
                "ends_at": datetime(2001, 1, 1, 1, tzinfo=UTC),
            },
            "keep",
        ),
        ({"status": "no_show"}, "keep"),
        ({"status": "cancelled"}, "remove"),
        ({"status": "pending"}, "remove"),
        ({"replaces_booking_id": uuid.uuid4()}, "remove"),
    ],
)
def test_what_a_booking_calls_for(over: dict[str, Any], plan: str) -> None:
    assert plan_for(record(**over), datetime(2031, 1, 1, tzinfo=UTC)) == plan


def test_every_statement_that_changes_a_booking_raises_its_version() -> None:
    """The ledger and the job trust `version`: a status or time written without it would leave a
    stale event forever.
    """
    watched = re.compile(r"\b(status|starts_at|ends_at)\s*=", re.IGNORECASE)
    for path in sorted((REPO_ROOT / "db" / "sql").rglob("*.sql")):
        text = path.read_text(encoding="utf-8")
        for statement in re.findall(
            r"UPDATE\s+(?:public\.)?bookings\b(.*?)(?:;|\Z)", text, re.S | re.I
        ):
            assigned = re.split(r"\bWHERE\b", statement, flags=re.I)[0]
            if watched.search(assigned):
                assert re.search(r"version\s*=\s*(?:\w+\.)?version\s*\+\s*1", assigned), path.name
    for path in (REPO_ROOT / "csa").rglob("*.py"):
        text = Path(path).read_text(encoding="utf-8")
        assert not re.search(r"UPDATE\s+(?:public\.)?bookings\b", text, re.I), (
            f"{path.name}: write bookings from a .sql file, so this scan sees it"
        )
