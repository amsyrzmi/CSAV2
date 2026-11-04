"""Bookings setup API: people, rooms, types, hours, closed days and blocks (stage 1, PR 1)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import httpx
import pytest
from csa.bookings.api import build_bookings_router
from csa.bookings.availability import DEFAULT_COMPANY_HOURS

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/bookings"
WEEKDAYS = [{"weekday": day, "opens": "09:00", "closes": "18:00"} for day in range(5)]


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_bookings_router())
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "bookings")


class Team:
    """One Admin, one staff member and one read-only member of the first company."""

    def __init__(self, w: SessionWorld) -> None:
        self.w = w

    async def open(self) -> Team:
        self.admin, self.admin_info = await self.w.member(role="tenant_admin")
        self.staff, self.staff_info = await self.w.member(role="tenant_staff")
        self.reader, self.reader_info = await self.w.member(role="read_only")
        self.other, _ = await self.w.member(role="tenant_admin", tenant=self.w.tenants[1])
        return self

    async def send(
        self, who: httpx.AsyncClient, path: str, body: Any = None, method: str = "POST"
    ) -> httpx.Response:
        return await self.w.mutate(who, f"{BASE}{path}", body, method=method)


@pytest.fixture
async def team(sessions: SessionWorld) -> Team:
    return await Team(sessions).open()


async def make_person(t: Team, name: str = "Aina Salleh", **extra: Any) -> dict[str, Any]:
    made = await t.send(t.admin, "/people", {"display_name": name, **extra})
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


async def make_resource(t: Team, name: str = "Room 1", **extra: Any) -> dict[str, Any]:
    made = await t.send(t.admin, "/resources", {"name": name, **extra})
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


TYPE = {"name": "Haircut", "duration_minutes": 30, "location_kind": "phone"}


async def make_type(t: Team, **extra: Any) -> dict[str, Any]:
    made = await t.send(t.admin, "/types", {**TYPE, **extra})
    assert made.status_code == 201, made.text
    return made.json()  # type: ignore[no-any-return]


# --- roles, company isolation and the package gate ---------------------------------------


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/people", {"display_name": "Aina Salleh"}),
        ("/resources", {"name": "Room 1"}),
        ("/types", TYPE),
        ("/closed-days", {"day": "2031-01-01"}),
    ],
)
async def test_each_setup_list_roles_and_company_isolation(
    sessions: SessionWorld, team: Team, path: str, body: dict[str, Any]
) -> None:
    w = sessions
    assert (await team.send(team.admin, path, body)).status_code == 201
    assert (await team.send(team.staff, path, body)).status_code == 403
    assert (await team.send(team.reader, path, body)).status_code == 403
    for reads in (team.admin, team.staff, team.reader):
        shown = await reads.get(f"{BASE}{path}")
        assert shown.status_code == 200 and len(shown.json()) == 1
    assert (await team.other.get(f"{BASE}{path}")).json() == []
    # Another company's id is refused outright.
    assert (await team.other.get(f"{BASE}{path}?client_id={w.tenants[0]}")).status_code == 403
    # A platform admin who picked the company may write.
    chosen = await w.mutate(w.admin, f"{BASE}{path}?client_id={w.tenants[1]}", body)
    assert chosen.status_code == 201, chosen.text


async def test_hours_and_blocks_follow_the_same_roles(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    put = {"periods": WEEKDAYS}
    assert (await team.send(team.admin, "/hours", put, "PUT")).status_code == 200
    assert (await team.send(team.staff, "/hours", put, "PUT")).status_code == 403
    assert (await team.send(team.reader, "/hours", put, "PUT")).status_code == 403
    block = {
        "subject_kind": "person",
        "subject_id": person["id"],
        "starts_at": "2031-05-05T02:00:00Z",
        "ends_at": "2031-05-05T03:00:00Z",
    }
    made = await team.send(team.admin, "/blocks", block)
    assert made.status_code == 201, made.text
    assert (await team.send(team.staff, "/blocks", block)).status_code == 403
    assert (
        await team.send(team.staff, f"/blocks/{made.json()['id']}", None, "DELETE")
    ).status_code == 403
    for reads in (team.staff, team.reader):
        assert (await reads.get(f"{BASE}/hours")).status_code == 200
        assert (await reads.get(f"{BASE}/blocks")).status_code == 200
    assert (await team.other.get(f"{BASE}/blocks")).json() == []
    # The other company cannot delete or see the block, whatever it knows of its id.
    gone = await team.send(team.other, f"/blocks/{made.json()['id']}", None, "DELETE")
    assert gone.status_code == 404


async def test_an_inactive_package_gets_the_gate_refusal(
    sessions: SessionWorld, team: Team
) -> None:
    w = sessions
    off = await w.mutate(
        w.admin, f"/console/v1/services/bookings/suspend?client_id={w.tenants[0]}", {}
    )
    assert off.status_code == 200, off.text
    for who in (team.admin, team.staff, team.reader):
        refused = await who.get(f"{BASE}/people")
        assert refused.status_code == 403 and refused.json()["detail"] == "package_inactive"
    refused = await team.send(team.admin, "/people", {"display_name": "Aina"})
    assert refused.status_code == 403 and refused.json()["detail"] == "package_inactive"


# --- people ----------------------------------------------------------------------------------


async def test_a_person_is_linked_to_a_member_and_an_hr_person(
    sessions: SessionWorld, team: Team
) -> None:
    w = sessions
    tenant = w.tenants[0]
    employee = str(uuid.uuid4())
    await w.q(
        "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s,%s,'Synthetic')",
        tenant,
        employee,
    )
    user = team.staff_info["user"]["id"]
    made = await make_person(
        team, "Staff Person", user_id=user, employee_id=employee, email="staff@example.test"
    )
    assert made["user_id"] == user and made["employee_id"] == employee
    assert made["version"] == 1 and made["is_active"] is True

    again = await team.send(team.admin, "/people", {"display_name": "Twice", "user_id": user})
    assert again.status_code == 409 and again.json()["detail"] == "person_exists"
    stranger = await team.send(
        team.admin, "/people", {"display_name": "Stranger", "user_id": str(uuid.uuid4())}
    )
    assert stranger.status_code == 422 and stranger.json()["detail"] == "member_not_found"
    # A member of another company is no member of this one.
    foreign = await team.send(
        team.admin, "/people", {"display_name": "Foreign", "user_id": (await _other_user(w))}
    )
    assert foreign.status_code == 422 and foreign.json()["detail"] == "member_not_found"
    unknown = await team.send(
        team.admin, "/people", {"display_name": "Ghost", "employee_id": str(uuid.uuid4())}
    )
    assert unknown.status_code == 422 and unknown.json()["detail"] == "employee_not_found"
    # An HR person of another company is no HR person of this one.
    other_employee = str(uuid.uuid4())
    await w.q(
        "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s,%s,'Elsewhere')",
        w.tenants[1],
        other_employee,
    )
    elsewhere = await team.send(
        team.admin, "/people", {"display_name": "Else", "employee_id": other_employee}
    )
    assert elsewhere.status_code == 422 and elsewhere.json()["detail"] == "employee_not_found"


async def _other_user(w: SessionWorld) -> str:
    rows = await w.q(
        "SELECT user_id FROM console_memberships WHERE client_id=%s LIMIT 1", w.tenants[1]
    )
    return str(rows[0]["user_id"])


async def test_a_stale_version_gets_409(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    resource = await make_resource(team)
    kind = await make_type(team)
    for path, made, change in (
        ("/people", person, {"display_name": "Renamed"}),
        ("/resources", resource, {"name": "Renamed"}),
        ("/types", kind, {"name": "Renamed"}),
    ):
        url = f"{path}/{made['id']}"
        first = await team.send(team.admin, url, {"version": 1, **change}, "PATCH")
        assert first.status_code == 200, first.text
        assert first.json()["version"] == 2
        stale = await team.send(team.admin, url, {"version": 1, **change}, "PATCH")
        assert stale.status_code == 409 and stale.json()["detail"] == "version_conflict"
        assert (
            await team.send(team.staff, url, {"version": 2, **change}, "PATCH")
        ).status_code == 403
        missing = await team.send(
            team.admin, f"{path}/{uuid.uuid4()}", {"version": 1, **change}, "PATCH"
        )
        assert missing.status_code == 404


async def test_a_person_can_be_deactivated_and_unlinked(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team, email="aina@example.test")
    patched = await team.send(
        team.admin,
        f"/people/{person['id']}",
        {"version": 1, "is_active": False, "email": None},
        "PATCH",
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["is_active"] is False and patched.json()["email"] is None
    kept = await team.send(
        team.admin, f"/people/{person['id']}", {"version": 2, "display_name": "Aina S."}, "PATCH"
    )
    assert kept.json()["is_active"] is False and kept.json()["display_name"] == "Aina S."


async def test_from_team_adds_members_once_and_links_hr(sessions: SessionWorld, team: Team) -> None:
    w = sessions
    tenant = w.tenants[0]
    employee = str(uuid.uuid4())
    staff_user = team.staff_info["user"]["id"]
    await w.q(
        "INSERT INTO hr_employees (client_id, id, display_name) VALUES (%s,%s,'Synthetic')",
        tenant,
        employee,
    )
    await w.q(
        "INSERT INTO hr_employee_accounts (client_id, user_id, employee_id) VALUES (%s,%s,%s)",
        tenant,
        staff_user,
        employee,
    )
    # Someone already listed by hand is not listed twice.
    reader_user = team.reader_info["user"]["id"]
    await make_person(team, "Listed by hand", user_id=reader_user)

    # The Admin has no HR grant: the link still shows.
    added = await team.send(team.admin, "/people/from-team")
    assert added.status_code == 200, added.text
    names = {row["user_id"]: row for row in added.json()}
    assert set(names) == {team.admin_info["user"]["id"], staff_user}
    assert names[staff_user]["employee_id"] == employee
    assert names[team.admin_info["user"]["id"]]["employee_id"] is None
    assert names[staff_user]["email"].endswith("@example.test")

    again = await team.send(team.admin, "/people/from-team")
    assert again.status_code == 200 and again.json() == []
    listed = await team.admin.get(f"{BASE}/people")
    assert len(listed.json()) == 3

    assert (await team.send(team.staff, "/people/from-team")).status_code == 403
    assert (await team.send(team.reader, "/people/from-team")).status_code == 403


async def test_from_team_works_for_a_company_with_no_hr(sessions: SessionWorld, team: Team) -> None:
    added = await team.send(team.other, "/people/from-team")
    assert added.status_code == 200, added.text
    assert [row["employee_id"] for row in added.json()] == [None]
    assert (await team.admin.get(f"{BASE}/people")).json() == []


# --- rooms -----------------------------------------------------------------------------------


async def test_resources_validate_and_patch(sessions: SessionWorld, team: Team) -> None:
    room = await make_resource(team, "Meeting room", capacity=6)
    assert room["capacity"] == 6 and room["is_active"] is True and room["version"] == 1
    for bad in ({"name": ""}, {"name": "a\nb"}, {"name": "x" * 121}, {"name": "ok", "capacity": 0}):
        refused = await team.send(team.admin, "/resources", bad)
        assert refused.status_code == 422, bad
    patched = await team.send(
        team.admin, f"/resources/{room['id']}", {"version": 1, "capacity": None}, "PATCH"
    )
    assert patched.json()["capacity"] is None and patched.json()["name"] == "Meeting room"


# --- types -----------------------------------------------------------------------------------


async def test_starter_types_are_created_once(sessions: SessionWorld, team: Team) -> None:
    first = await team.send(team.admin, "/types/starters")
    assert first.status_code == 201, first.text
    made = first.json()
    assert [(t["name"], t["duration_minutes"]) for t in made] == [
        ("Appointment", 30),
        ("Consultation", 60),
    ]
    assert all(t["audience"] == "customers" and t["delivery"] == "any" for t in made)
    second = await team.send(team.admin, "/types/starters")
    assert second.status_code == 200
    assert [t["id"] for t in second.json()] == [t["id"] for t in made]
    assert len((await team.admin.get(f"{BASE}/types")).json()) == 2
    assert (await team.send(team.staff, "/types/starters")).status_code == 403
    # A company that has its own type gets no starters.
    third = await team.send(team.other, "/types", TYPE)
    assert third.status_code == 201
    none = await team.send(team.other, "/types/starters")
    assert none.status_code == 200 and [t["name"] for t in none.json()] == ["Haircut"]


@pytest.mark.parametrize(
    ("extra", "detail"),
    [
        ({"delivery": "named"}, "type_needs_people"),
        ({"delivery": "several"}, "type_needs_people"),
        ({"resource_rule": "specific"}, "type_needs_resources"),
        ({"resource_rule": "any"}, "type_needs_resources"),
        ({"location_kind": "in_person"}, "type_needs_address"),
        ({"location_kind": "online"}, "type_needs_link"),
        ({"person_ids": [str(uuid.uuid4())]}, "person_not_found"),
        ({"resource_ids": [str(uuid.uuid4())], "resource_rule": "any"}, "resource_not_found"),
    ],
)
async def test_a_type_needs_what_its_rules_name(
    sessions: SessionWorld, team: Team, extra: dict[str, Any], detail: str
) -> None:
    refused = await team.send(team.admin, "/types", {**TYPE, **extra})
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"] == detail


@pytest.mark.parametrize(
    "extra",
    [
        {"name": "two\nlines"},
        {"name": "x" * 121},
        {"location_kind": "in_person", "address": "1 Jalan\nTest"},
        {"location_kind": "in_person", "address": "x" * 301},
        {"location_kind": "in_person", "address": "   "},
        {"location_kind": "online", "meeting_link": "http://meet.example.test/x"},
        {"location_kind": "online", "meeting_link": "https://meet.example.test/a b"},
        {"location_kind": "online", "meeting_link": "https://meet.example.test/\r\nBcc: x"},
        {"location_kind": "online", "meeting_link": "https://" + "a" * 500},
        {"duration_minutes": 0},
        {"audience": "everyone"},
    ],
)
async def test_type_text_is_one_short_line(
    sessions: SessionWorld, team: Team, extra: dict[str, Any]
) -> None:
    refused = await team.send(team.admin, "/types", {**TYPE, **extra})
    assert refused.status_code == 422, extra


async def test_a_type_keeps_its_people_and_rooms(sessions: SessionWorld, team: Team) -> None:
    a = await make_person(team, "Aina")
    b = await make_person(team, "Bakar")
    room = await make_resource(team)
    made = await make_type(
        team,
        delivery="several",
        person_ids=[a["id"], b["id"]],
        resource_rule="specific",
        resource_ids=[room["id"]],
        location_kind="in_person",
        address="  1 Jalan Contoh, Kuala Lumpur ",
        meeting_link="https://ignored.example.test",
    )
    assert sorted(made["person_ids"]) == sorted([a["id"], b["id"]])
    assert made["resource_ids"] == [room["id"]]
    assert made["address"] == "1 Jalan Contoh, Kuala Lumpur" and made["meeting_link"] is None

    # Changing the rule alone keeps the rule honest: "named" with nobody left is refused.
    emptied = await team.send(
        team.admin, f"/types/{made['id']}", {"version": 1, "person_ids": []}, "PATCH"
    )
    assert emptied.status_code == 422 and emptied.json()["detail"] == "type_needs_people"
    switched = await team.send(
        team.admin,
        f"/types/{made['id']}",
        {"version": 1, "delivery": "any", "person_ids": [], "resource_rule": "none"},
        "PATCH",
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["person_ids"] == [] and switched.json()["resource_ids"] == []
    listed = (await team.reader.get(f"{BASE}/types")).json()
    assert [t["id"] for t in listed] == [made["id"]]


# --- hours -----------------------------------------------------------------------------------


async def test_hours_default_to_weekdays_and_the_default_is_not_shared(
    sessions: SessionWorld, team: Team
) -> None:
    shown = await team.reader.get(f"{BASE}/hours")
    assert shown.status_code == 200
    assert shown.json() == {
        "subject_kind": "company",
        "subject_id": None,
        "is_default": True,
        "periods": WEEKDAYS,
    }
    # The shared default is never handed out for the caller to change.
    from csa.bookings.setup import default_periods

    default_periods().clear()
    assert sorted(DEFAULT_COMPANY_HOURS) == [0, 1, 2, 3, 4]
    assert len(default_periods()) == 5
    assert (await team.reader.get(f"{BASE}/hours")).json()["periods"] == WEEKDAYS


async def test_hours_are_replaced_as_a_whole(sessions: SessionWorld, team: Team) -> None:
    periods = [
        {"weekday": 0, "opens": "08:00", "closes": "12:00"},
        {"weekday": 0, "opens": "13:00", "closes": "23:59"},
        {"weekday": 5, "opens": "10:00", "closes": "14:00"},
    ]
    saved = await team.send(team.admin, "/hours", {"periods": periods}, "PUT")
    assert saved.status_code == 200, saved.text
    assert saved.json()["is_default"] is False and saved.json()["periods"] == periods
    assert (await team.reader.get(f"{BASE}/hours")).json()["periods"] == periods
    assert (await team.other.get(f"{BASE}/hours")).json()["is_default"] is True

    narrower = await team.send(team.admin, "/hours", {"periods": periods[2:]}, "PUT")
    assert narrower.json()["periods"] == periods[2:]
    # Nothing at all goes back to the default week.
    reset = await team.send(team.admin, "/hours", {"periods": []}, "PUT")
    assert reset.json()["is_default"] is True and reset.json()["periods"] == WEEKDAYS


@pytest.mark.parametrize(
    "periods",
    [
        [{"weekday": 0, "opens": "18:00", "closes": "09:00"}],
        [{"weekday": 0, "opens": "22:00", "closes": "02:00"}],
        [{"weekday": 0, "opens": "09:00", "closes": "09:00"}],
        [{"weekday": 0, "opens": "09:00", "closes": "24:00"}],
        [{"weekday": 0, "opens": "9:00", "closes": "17:00"}],
        [{"weekday": 7, "opens": "09:00", "closes": "17:00"}],
        [
            {"weekday": 1, "opens": "09:00", "closes": "13:00"},
            {"weekday": 1, "opens": "12:00", "closes": "17:00"},
        ],
    ],
)
async def test_hours_that_do_not_open_before_they_close_are_refused(
    sessions: SessionWorld, team: Team, periods: list[dict[str, Any]]
) -> None:
    refused = await team.send(team.admin, "/hours", {"periods": periods}, "PUT")
    assert refused.status_code == 422, refused.text
    assert (await team.admin.get(f"{BASE}/hours")).json()["is_default"] is True


async def test_a_person_or_room_can_have_hours_of_their_own(
    sessions: SessionWorld, team: Team
) -> None:
    person = await make_person(team)
    room = await make_resource(team)
    own = [{"weekday": 2, "opens": "10:00", "closes": "16:00"}]
    for kind, subject in (("person", person), ("resource", room)):
        query = f"?subject_kind={kind}&subject_id={subject['id']}"
        before = (await team.reader.get(f"{BASE}/hours{query}")).json()
        assert before["periods"] == [] and before["is_default"] is True
        body = {"subject_kind": kind, "subject_id": subject["id"], "periods": own}
        saved = await team.send(team.admin, "/hours", body, "PUT")
        assert saved.status_code == 200, saved.text
        assert saved.json()["periods"] == own and saved.json()["is_default"] is False
        assert (await team.reader.get(f"{BASE}/hours{query}")).json()["periods"] == own
        cleared = await team.send(team.admin, "/hours", {**body, "periods": []}, "PUT")
        assert cleared.json()["periods"] == []
    # The company's own week is untouched by all of that.
    assert (await team.reader.get(f"{BASE}/hours")).json()["is_default"] is True


async def test_hours_for_someone_outside_the_company_are_refused(
    sessions: SessionWorld, team: Team
) -> None:
    w = sessions
    elsewhere = await team.send(team.other, "/people", {"display_name": "Other"})
    foreign = elsewhere.json()["id"]
    own = [{"weekday": 2, "opens": "10:00", "closes": "16:00"}]
    for body in (
        {"subject_kind": "person", "subject_id": foreign, "periods": own},
        {"subject_kind": "person", "subject_id": str(uuid.uuid4()), "periods": own},
        {"subject_kind": "resource", "subject_id": foreign, "periods": own},
        {"subject_kind": "person", "periods": own},
        {"subject_kind": "company", "subject_id": foreign, "periods": own},
    ):
        refused = await team.send(team.admin, "/hours", body, "PUT")
        assert refused.status_code == 422, body
    query = f"?subject_kind=person&subject_id={foreign}"
    assert (await team.admin.get(f"{BASE}/hours{query}")).status_code == 422
    rows = await w.q("SELECT count(*) AS n FROM booking_hours WHERE client_id=%s", w.tenants[0])
    assert rows[0]["n"] == 0


# --- closed days -----------------------------------------------------------------------------


async def test_closed_days_are_added_listed_and_removed(sessions: SessionWorld, team: Team) -> None:
    made = await team.send(team.admin, "/closed-days", {"day": "2031-08-31", "note": "Merdeka"})
    assert made.status_code == 201 and made.json() == {"day": "2031-08-31", "note": "Merdeka"}
    # Adding it again changes the note and does not fail.
    again = await team.send(
        team.admin, "/closed-days", {"day": "2031-08-31", "note": "National Day"}
    )
    assert again.status_code == 201 and again.json()["note"] == "National Day"
    await team.send(team.admin, "/closed-days", {"day": "2031-01-01"})
    listed = (await team.reader.get(f"{BASE}/closed-days")).json()
    assert [row["day"] for row in listed] == ["2031-01-01", "2031-08-31"]
    ranged = (await team.reader.get(f"{BASE}/closed-days?from=2031-06-01&to=2031-12-31")).json()
    assert [row["day"] for row in ranged] == ["2031-08-31"]
    for bad in (
        {"day": "tomorrow"},
        {"day": "2031-01-01", "note": "a\nb"},
        {"day": "2031-01-01", "note": "x" * 201},
    ):
        assert (await team.send(team.admin, "/closed-days", bad)).status_code == 422

    assert (
        await team.send(team.staff, "/closed-days/2031-08-31", None, "DELETE")
    ).status_code == 403
    gone = await team.send(team.admin, "/closed-days/2031-08-31", None, "DELETE")
    assert gone.status_code == 204
    missing = await team.send(team.admin, "/closed-days/2031-08-31", None, "DELETE")
    assert missing.status_code == 404
    assert [r["day"] for r in (await team.reader.get(f"{BASE}/closed-days")).json()] == [
        "2031-01-01"
    ]


# --- blocks ----------------------------------------------------------------------------------


def block_body(subject: dict[str, Any], kind: str = "person", **extra: Any) -> dict[str, Any]:
    return {
        "subject_kind": kind,
        "subject_id": subject["id"],
        "starts_at": "2031-05-05T02:00:00Z",
        "ends_at": "2031-05-05T03:00:00Z",
        **extra,
    }


async def test_a_block_writes_its_hold_and_delete_frees_it(
    sessions: SessionWorld, team: Team
) -> None:
    w = sessions
    person = await make_person(team)
    room = await make_resource(team)
    for kind, subject in (("person", person), ("resource", room)):
        made = await team.send(team.admin, "/blocks", block_body(subject, kind, note="Dentist"))
        assert made.status_code == 201, made.text
        out = made.json()
        assert out["subject_kind"] == kind and out["subject_id"] == subject["id"]
        assert out["note"] == "Dentist" and out["starts_at"].startswith("2031-05-05T02:00:00")
        holds = await w.q(
            "SELECT count(*) AS n FROM booking_holds WHERE block_id=%s AND subject_id=%s",
            out["id"],
            subject["id"],
        )
        assert holds[0]["n"] == 1
        clash = await team.send(team.admin, "/blocks", block_body(subject, kind))
        assert clash.status_code == 409 and clash.json()["detail"] == "slot_taken"
        shown = (await team.reader.get(f"{BASE}/blocks")).json()
        assert out["id"] in [b["id"] for b in shown]
        gone = await team.send(team.admin, f"/blocks/{out['id']}", None, "DELETE")
        assert gone.status_code == 204
        assert (
            await team.send(team.admin, f"/blocks/{out['id']}", None, "DELETE")
        ).status_code == 404
        holds = await w.q("SELECT count(*) AS n FROM booking_holds WHERE block_id=%s", out["id"])
        assert holds[0]["n"] == 0
        again = await team.send(team.admin, "/blocks", block_body(subject, kind))
        assert again.status_code == 201
        await team.send(team.admin, f"/blocks/{again.json()['id']}", None, "DELETE")


async def test_a_block_over_a_booking_is_refused(sessions: SessionWorld, team: Team) -> None:
    w = sessions
    tenant = w.tenants[0]
    person = await make_person(team)
    booking = str(uuid.uuid4())
    await w.q(
        "INSERT INTO bookings (id, client_id, service, starts_at, ends_at)"
        " VALUES (%s,%s,'Checkup','2031-03-01T01:00Z','2031-03-01T01:30Z')",
        booking,
        tenant,
    )
    await w.q(
        "INSERT INTO booking_holds (client_id, booking_id, subject_kind, subject_id, during)"
        " VALUES (%s,%s,'person',%s, tstzrange('2031-03-01T01:00Z','2031-03-01T01:30Z'))",
        tenant,
        booking,
        person["id"],
    )
    over = {"starts_at": "2031-03-01T01:15:00Z", "ends_at": "2031-03-01T02:00:00Z"}
    refused = await team.send(team.admin, "/blocks", block_body(person, **over))
    assert refused.status_code == 409 and refused.json()["detail"] == "slot_taken"
    rows = await w.q("SELECT count(*) AS n FROM booking_blocks WHERE client_id=%s", tenant)
    assert rows[0]["n"] == 0
    # Next to it is fine, and so is the same time for someone else.
    after = {"starts_at": "2031-03-01T01:30:00Z", "ends_at": "2031-03-01T02:00:00Z"}
    assert (await team.send(team.admin, "/blocks", block_body(person, **after))).status_code == 201
    other = await make_person(team, "Bakar")
    assert (await team.send(team.admin, "/blocks", block_body(other, **over))).status_code == 201


async def test_two_blocks_at_once_leave_one(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    results = await asyncio.gather(
        *[team.send(team.admin, "/blocks", block_body(person)) for _ in range(6)]
    )
    codes = sorted(r.status_code for r in results)
    assert codes == [201, 409, 409, 409, 409, 409], [r.text for r in results]
    assert all(r.json()["detail"] == "slot_taken" for r in results if r.status_code == 409)


async def test_a_block_must_make_sense(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    elsewhere = (await team.send(team.other, "/people", {"display_name": "Other"})).json()
    same = block_body(person, ends_at="2031-05-05T02:00:00Z")
    backwards = block_body(person, starts_at="2031-05-05T04:00:00Z")
    naive = block_body(person, starts_at="2031-05-05T02:00:00")
    for body in (
        same,
        backwards,
        naive,
        block_body(elsewhere),
        block_body({"id": str(uuid.uuid4())}),
        block_body(person, kind="resource"),
        block_body(person, note="a\nb"),
        block_body(person, kind="company"),
    ):
        refused = await team.send(team.admin, "/blocks", body)
        assert refused.status_code == 422, body
    assert (await team.reader.get(f"{BASE}/blocks")).json() == []


async def test_blocks_list_by_window(sessions: SessionWorld, team: Team) -> None:
    person = await make_person(team)
    for day in ("05", "06", "07"):
        body = block_body(
            person, starts_at=f"2031-05-{day}T02:00:00Z", ends_at=f"2031-05-{day}T03:00:00Z"
        )
        assert (await team.send(team.admin, "/blocks", body)).status_code == 201
    window = "?from=2031-05-06T00:00:00Z&to=2031-05-07T00:00:00Z"
    shown = (await team.reader.get(f"{BASE}/blocks{window}")).json()
    assert [b["starts_at"][:10] for b in shown] == ["2031-05-06"]


# --- audit -----------------------------------------------------------------------------------


async def test_each_setup_change_writes_an_audit_row(sessions: SessionWorld, team: Team) -> None:
    w = sessions
    person = await make_person(team)
    await team.send(team.admin, f"/people/{person['id']}", {"version": 1, "email": None}, "PATCH")
    await team.send(team.admin, "/people/from-team")
    room = await make_resource(team)
    await team.send(team.admin, f"/resources/{room['id']}", {"version": 1, "name": "R2"}, "PATCH")
    kind = await make_type(team)
    await team.send(team.admin, f"/types/{kind['id']}", {"version": 1, "name": "T2"}, "PATCH")
    await team.send(team.admin, "/types/starters")  # creates nothing: one type exists
    await team.send(team.other, "/types/starters")
    await team.send(team.admin, "/hours", {"periods": WEEKDAYS}, "PUT")
    await team.send(team.admin, "/closed-days", {"day": "2031-01-01"})
    await team.send(team.admin, "/closed-days/2031-01-01", None, "DELETE")
    made = await team.send(team.admin, "/blocks", block_body(person))
    await team.send(team.admin, f"/blocks/{made.json()['id']}", None, "DELETE")
    # Things that fail leave no row.
    await team.send(team.admin, f"/people/{person['id']}", {"version": 1, "email": None}, "PATCH")
    await team.send(team.staff, "/resources", {"name": "Refused"})

    rows = await w.q(
        "SELECT action, target_id FROM console_audit WHERE client_id=%s AND action LIKE %s"
        " ORDER BY created_at, id",
        w.tenants[0],
        "bookings.%",
    )
    actions = [row["action"] for row in rows]
    assert actions[:7] == [
        "bookings.person.created",
        "bookings.person.updated",
        *["bookings.person.created"] * 3,  # from-team: the Admin, staff and read-only member
        "bookings.resource.created",
        "bookings.resource.updated",
    ]
    assert actions[7:] == [
        "bookings.type.created",
        "bookings.type.updated",
        "bookings.hours.replaced",
        "bookings.closed_day.added",
        "bookings.closed_day.removed",
        "bookings.block.created",
        "bookings.block.removed",
    ]
    targets = {row["action"]: row["target_id"] for row in rows}
    assert targets["bookings.closed_day.added"] == "2031-01-01"
    assert targets["bookings.resource.created"] == room["id"]
    other_rows = await w.q(
        "SELECT action FROM console_audit WHERE client_id=%s AND action LIKE %s",
        w.tenants[1],
        "bookings.%",
    )
    assert [r["action"] for r in other_rows] == ["bookings.type.created", "bookings.type.created"]
