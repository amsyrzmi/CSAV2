"""Connecting a person's Google Calendar: who may, the encrypted grant, the one calendar, and what
disconnecting and a refused refresh token do. A fake Google stands in for the real one.
"""

from __future__ import annotations

import base64
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from csa.bookings.api import build_bookings_router
from csa.bookings.google.api import build_calendar_router
from csa.bookings.google.client import CALENDAR_SCOPE, CalendarError, TokenSet
from csa.bookings.google.connections import CalendarConnections
from csa.core import crypto, settings
from pydantic import SecretStr

from tests.conftest import requires_docker
from tests.test_bookings_setup import Team, make_person
from tests.test_console_sessions import SessionWorld
from tests.test_customer_replies import activate_only

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/bookings/calendar"
BOOKINGS = "/portal/connections"
TABLE = "booking_calendar_connections"


class FakeCalendar:
    """Google as far as connecting goes, recording what it was asked."""

    def __init__(self) -> None:
        self.scopes: tuple[str, ...] = (CALENDAR_SCOPE,)
        self.refresh_error: str | None = None
        self.exists = True
        self.created: list[tuple[str, str]] = []
        self.revoked: list[str] = []
        self.exchanged: list[str] = []

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://accounts.example.test/auth?state={state}&challenge={code_challenge}"

    async def exchange(self, *, code: str, code_verifier: str) -> TokenSet:
        self.exchanged.append(code_verifier)
        if CALENDAR_SCOPE not in self.scopes:
            raise CalendarError("scopes_not_granted")
        return TokenSet(
            SecretStr(f"access-{code}"), SecretStr(f"refresh-{code}"), 3600, self.scopes
        )

    async def refresh(self, refresh_token: SecretStr) -> TokenSet:
        if self.refresh_error:
            raise CalendarError(self.refresh_error)
        return TokenSet(SecretStr("access-refreshed"), None, 3600, ())

    async def revoke(self, token: SecretStr) -> None:
        self.revoked.append(token.get_secret_value())

    async def create_calendar(self, access: SecretStr, *, summary: str, time_zone: str) -> str:
        self.created.append((summary, time_zone))
        return f"calendar-{len(self.created)}"

    async def calendar_exists(self, access: SecretStr, calendar_id: str) -> bool:
        return self.exists


@pytest.fixture
def google() -> FakeCalendar:
    return FakeCalendar()


@pytest.fixture(autouse=True)
def credentials_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A synthetic encryption key and a valid Settings env, as tests/test_crypto.py does."""
    key = "base64:" + base64.b64encode(b"\x07" * 32).decode("ascii")
    for name, value in (
        ("ENV", "ci"),
        ("DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa"),
        ("LG_DATABASE_URL", "postgresql://csa_app:pw@localhost:5433/csa"),
        ("GROQ_API_KEY", "x"),
        ("PUBLIC_BASE_URL", "https://example.test"),
        ("CREDENTIALS_KEY", key),
    ):
        monkeypatch.setenv(name, value)
    settings.get_settings.cache_clear()
    yield
    settings.get_settings.cache_clear()


@pytest.fixture(autouse=True)
async def prepared(sessions: SessionWorld, google: FakeCalendar) -> None:
    sessions.app.include_router(build_bookings_router())
    sessions.app.include_router(build_calendar_router())
    sessions.app.state.calendar_connections = CalendarConnections(
        sessions.pool,
        google,
        enabled=True,  # type: ignore[arg-type]
    )
    for tenant in sessions.tenants:
        await activate_only(sessions, tenant, "bookings")
        await sessions.q("UPDATE clients SET timezone = 'Asia/Kuala_Lumpur' WHERE id = %s", tenant)


@pytest.fixture
async def team(sessions: SessionWorld) -> Team:
    return await Team(sessions).open()


@pytest.fixture
async def person(team: Team) -> dict[str, Any]:
    """The staff member, listed as a bookable person linked to their account."""
    return await make_person(team, "Staff Person", user_id=team.staff_info["user"]["id"])


async def start(team: Team, who: httpx.AsyncClient | None = None) -> httpx.Response:
    return await team.send(who or team.staff, "/calendar/connect")


async def connect(team: Team, google: FakeCalendar, code: str = "c1") -> str:
    """Start and finish a connection as the staff member; the stored connection's id."""
    started = await start(team)
    assert started.status_code == 200, started.text
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    done = await team.staff.get(
        f"{BASE}/callback", params={"code": code, "state": state}, follow_redirects=False
    )
    assert done.status_code == 303, done.text
    assert done.headers["location"] == f"{BOOKINGS}?calendar=connected", done.headers["location"]
    rows = await team.w.q(
        "SELECT id FROM booking_calendar_connections WHERE client_id = %s", team.w.tenants[0]
    )
    return str(rows[0]["id"])


async def row(team: Team) -> dict[str, Any]:
    rows = await team.w.q(
        "SELECT status, calendar_id, access_token, refresh_token, token_expires_at, scopes"
        " FROM booking_calendar_connections WHERE client_id = %s",
        team.w.tenants[0],
    )
    assert len(rows) == 1
    return rows[0]


async def make_events(team: Team, person_id: str, statuses: list[str]) -> list[str]:
    """One booking per status with a ledger row for the person, written as the owner."""
    ids = []
    for index, status in enumerate(statuses):
        booking = str(uuid.uuid4())
        ids.append(booking)
        start = datetime(2031, 3, 4, 2, tzinfo=UTC) + timedelta(days=index)
        await team.w.q(
            "INSERT INTO bookings (id, client_id, service, starts_at, ends_at)"
            " VALUES (%s, %s, 'Synthetic', %s, %s)",
            booking,
            team.w.tenants[0],
            start,
            start + timedelta(minutes=30),
        )
        await team.w.q(
            "INSERT INTO booking_calendar_events (client_id, booking_id, person_id, status,"
            " google_event_id, synced_version) VALUES (%s, %s, %s, %s, %s, 1)",
            team.w.tenants[0],
            booking,
            person_id,
            status,
            f"event-{index}",
        )
    return ids


async def statuses(team: Team) -> dict[str, str]:
    rows = await team.w.q(
        "SELECT booking_id::text AS b, status FROM booking_calendar_events WHERE client_id = %s",
        team.w.tenants[0],
    )
    return {r["b"]: r["status"] for r in rows}


# --- who may connect ------------------------------------------------------------------------


async def test_a_member_linked_to_a_person_connects_for_themselves(
    team: Team, person: dict[str, Any]
) -> None:
    started = await start(team)
    assert started.status_code == 200, started.text
    query = parse_qs(urlsplit(started.json()["url"]).query)
    assert query["state"][0] and query["challenge"][0]
    [pending] = await team.w.q(
        "SELECT status FROM booking_calendar_connections WHERE client_id = %s", team.w.tenants[0]
    )
    assert pending["status"] == "pending"


async def test_an_admin_without_a_person_cannot_connect_for_anyone_else(
    team: Team, person: dict[str, Any]
) -> None:
    refused = await start(team, team.admin)
    assert refused.status_code == 409 and refused.json()["detail"] == "person_required"
    assert await team.w.q(f"SELECT 1 FROM {TABLE}") == []


async def test_read_only_members_other_companies_and_platform_admins_cannot_connect(
    team: Team, person: dict[str, Any]
) -> None:
    assert (await start(team, team.reader)).status_code == 403
    assert (await start(team, team.other)).status_code in {403, 409}
    platform = await team.w.mutate(
        team.w.admin,
        f"{BASE.replace('/calendar', '')}/calendar/connect?client_id={team.w.tenants[0]}",
    )
    assert platform.status_code in {403, 409}
    assert await team.w.q(f"SELECT 1 FROM {TABLE}") == []


async def test_a_deactivated_person_cannot_connect(team: Team, person: dict[str, Any]) -> None:
    await team.w.q("UPDATE booking_people SET is_active = false WHERE id = %s", person["id"])
    refused = await start(team)
    assert refused.status_code == 409 and refused.json()["detail"] == "person_required"


async def test_with_the_flag_off_nothing_is_available(
    sessions: SessionWorld, team: Team, person: dict[str, Any]
) -> None:
    sessions.app.state.calendar_connections = CalendarConnections(
        sessions.pool, None, enabled=False
    )
    status = await team.staff.get(f"{BASE}/status")
    assert status.status_code == 200
    assert status.json() == {
        "available": False,
        "can_connect": False,
        "status": None,
        "people": None,
    }
    refused = await start(team)
    assert refused.status_code == 503 and refused.json()["detail"] == "calendar_unavailable"
    assert await team.w.q(f"SELECT 1 FROM {TABLE}") == []


# --- status ---------------------------------------------------------------------------------


async def test_status_tells_a_member_their_own_state_and_an_admin_everyones(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    mine = (await team.staff.get(f"{BASE}/status")).json()
    assert mine == {
        "available": True,
        "can_connect": True,
        "status": "not_connected",
        "people": None,
    }
    nobody = (await team.reader.get(f"{BASE}/status")).json()
    assert (
        nobody["available"] is True and nobody["can_connect"] is False and nobody["people"] is None
    )
    await connect(team, google)
    assert (await team.staff.get(f"{BASE}/status")).json()["status"] == "connected"
    admin = (await team.admin.get(f"{BASE}/status")).json()
    assert admin["can_connect"] is False and admin["status"] is None
    assert admin["people"] == [
        {"person_id": person["id"], "name": "Staff Person", "status": "connected"}
    ]


async def test_another_company_sees_only_its_own_people(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    await connect(team, google)
    elsewhere = (await team.other.get(f"{BASE}/status")).json()
    assert elsewhere["people"] == []


# --- the callback ---------------------------------------------------------------------------


async def test_the_callback_stores_an_encrypted_grant_and_makes_the_calendar(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    connection = await connect(team, google)
    stored = await row(team)
    assert stored["status"] == "connected" and stored["calendar_id"] == "calendar-1"
    assert stored["scopes"] == [CALENDAR_SCOPE]
    assert stored["token_expires_at"] > datetime.now(UTC)
    for column, plain in (("access_token", "access-c1"), ("refresh_token", "refresh-c1")):
        assert plain not in stored[column] and stored[column].startswith("v1:")
        aad = crypto.credential_aad(TABLE, column, connection)
        assert crypto.decrypt(stored[column], aad) == plain
    [(summary, zone)] = google.created
    assert "Bookings" in summary and zone == "Asia/Kuala_Lumpur"


async def test_the_callback_works_only_once_and_only_for_the_one_who_started(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    started = await start(team)
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    params = {"code": "c1", "state": state}
    stranger = await team.admin.get(f"{BASE}/callback", params=params, follow_redirects=False)
    assert stranger.headers["location"] == f"{BOOKINGS}?calendar_error=oauth_state_invalid"
    first = await team.staff.get(f"{BASE}/callback", params=params, follow_redirects=False)
    assert first.headers["location"] == f"{BOOKINGS}?calendar=connected"
    again = await team.staff.get(f"{BASE}/callback", params=params, follow_redirects=False)
    assert again.headers["location"] == f"{BOOKINGS}?calendar_error=oauth_state_invalid"
    assert len(google.exchanged) == 1


async def test_a_wrong_expired_or_missing_state_is_refused(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    started = await start(team)
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    await team.w.q(
        "UPDATE booking_calendar_oauth_states SET expires_at = now() - interval '1 second'"
    )
    for params in ({"code": "c1", "state": state}, {"code": "c1", "state": "nope"}, {"code": "c1"}):
        got = await team.staff.get(f"{BASE}/callback", params=params, follow_redirects=False)
        assert got.headers["location"] == f"{BOOKINGS}?calendar_error=oauth_state_invalid"
    assert (await row(team))["status"] == "pending" and google.exchanged == []


async def test_a_denied_consent_or_a_foreign_issuer_is_a_plain_error(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    started = await start(team)
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    denied = await team.staff.get(
        f"{BASE}/callback",
        params={"error": "access_denied", "state": state},
        follow_redirects=False,
    )
    assert denied.headers["location"] == f"{BOOKINGS}?calendar_error=provider_denied"
    foreign = await team.staff.get(
        f"{BASE}/callback",
        params={"code": "c1", "state": state, "iss": "https://elsewhere.example.test"},
        follow_redirects=False,
    )
    assert foreign.headers["location"] == f"{BOOKINGS}?calendar_error=oauth_issuer_invalid"
    assert google.exchanged == [] and (await row(team))["status"] == "pending"


async def test_a_grant_without_the_calendar_scope_is_refused_and_stores_nothing(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    google.scopes = ("openid",)
    started = await start(team)
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    got = await team.staff.get(
        f"{BASE}/callback", params={"code": "c1", "state": state}, follow_redirects=False
    )
    assert got.headers["location"] == f"{BOOKINGS}?calendar_error=scopes_not_granted"
    stored = await row(team)
    assert stored["status"] == "pending" and stored["access_token"] is None and google.created == []


async def test_no_answer_holds_a_token(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    started = await start(team)
    state = parse_qs(urlsplit(started.json()["url"]).query)["state"][0]
    got = await team.staff.get(
        f"{BASE}/callback", params={"code": "c1", "state": state}, follow_redirects=False
    )
    bodies = [started.text, got.text, (await team.staff.get(f"{BASE}/status")).text]
    bodies.append((await team.admin.get(f"{BASE}/status")).text)
    for text in bodies:
        assert "access-c1" not in text and "refresh-c1" not in text and "v1:" not in text


# --- reconnecting and disconnecting ---------------------------------------------------------


async def test_disconnecting_revokes_clears_the_grant_and_drops_open_rows(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    await connect(team, google)
    a, b, c = await make_events(team, person["id"], ["synced", "pending", "deleted"])
    gone = await team.send(team.staff, "/calendar/disconnect")
    assert gone.status_code == 204, gone.text
    assert google.revoked == ["refresh-c1"]
    stored = await row(team)
    assert stored["status"] == "pending" and stored["calendar_id"] == "calendar-1"
    assert stored["access_token"] is None and stored["refresh_token"] is None
    assert await statuses(team) == {a: "dropped", b: "dropped", c: "deleted"}
    assert (await team.staff.get(f"{BASE}/status")).json()["status"] == "not_connected"


async def test_disconnecting_still_clears_the_grant_when_google_will_not_revoke(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    async def refuse(token: SecretStr) -> None:
        raise CalendarError("provider_unreachable")

    await connect(team, google)
    google.revoke = refuse  # type: ignore[method-assign]
    gone = await team.send(team.staff, "/calendar/disconnect")
    assert gone.status_code == 204
    assert (await row(team))["refresh_token"] is None


async def test_reconnecting_reuses_the_calendar_if_it_exists_and_makes_one_if_not(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    await connect(team, google)
    await team.send(team.staff, "/calendar/disconnect")
    await connect(team, google, "c2")
    assert (await row(team))["calendar_id"] == "calendar-1" and len(google.created) == 1
    await team.send(team.staff, "/calendar/disconnect")
    google.exists = False
    await connect(team, google, "c3")
    assert (await row(team))["calendar_id"] == "calendar-2" and len(google.created) == 2


async def test_reconnecting_brings_dropped_rows_back_to_pending(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    await connect(team, google)
    a, b = await make_events(team, person["id"], ["synced", "deleted"])
    await team.send(team.staff, "/calendar/disconnect")
    await connect(team, google, "c2")
    assert await statuses(team) == {a: "pending", b: "deleted"}


async def test_disconnecting_with_nothing_connected_is_a_plain_conflict(
    team: Team, person: dict[str, Any]
) -> None:
    gone = await team.send(team.staff, "/calendar/disconnect")
    assert gone.status_code == 409 and gone.json()["detail"] == "not_connected"


# --- the access token -----------------------------------------------------------------------


async def test_a_due_access_token_is_refreshed_and_stored_encrypted(
    sessions: SessionWorld, team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    connection = await connect(team, google)
    service: CalendarConnections = sessions.app.state.calendar_connections
    fresh = await service.access(uuid.UUID(sessions.tenants[0]), uuid.UUID(person["id"]))
    assert fresh.token.get_secret_value() == "access-c1" and fresh.calendar_id == "calendar-1"
    await sessions.q(
        "UPDATE booking_calendar_connections SET token_expires_at = now() - interval '1 minute'"
    )
    due = await service.access(uuid.UUID(sessions.tenants[0]), uuid.UUID(person["id"]))
    assert due.token.get_secret_value() == "access-refreshed"
    stored = await row(team)
    assert stored["token_expires_at"] > datetime.now(UTC)
    aad = crypto.credential_aad(TABLE, "access_token", connection)
    assert crypto.decrypt(stored["access_token"], aad) == "access-refreshed"
    assert (
        crypto.decrypt(
            stored["refresh_token"], crypto.credential_aad(TABLE, "refresh_token", connection)
        )
        == "refresh-c1"
    )


async def test_a_refused_refresh_token_needs_a_reconnect_and_is_reported(
    sessions: SessionWorld, team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    await connect(team, google)
    await sessions.q(
        "UPDATE booking_calendar_connections SET token_expires_at = now() - interval '1 minute'"
    )
    google.refresh_error = "invalid_grant"
    service: CalendarConnections = sessions.app.state.calendar_connections
    with pytest.raises(CalendarError) as caught:
        await service.access(uuid.UUID(sessions.tenants[0]), uuid.UUID(person["id"]))
    assert caught.value.code == "invalid_grant"
    assert (await row(team))["status"] == "needs_reconnect"
    assert (await team.staff.get(f"{BASE}/status")).json()["status"] == "needs_reconnect"
    # And no further attempts are made until they reconnect.
    google.refresh_error = None
    with pytest.raises(CalendarError) as again:
        await service.access(uuid.UUID(sessions.tenants[0]), uuid.UUID(person["id"]))
    assert again.value.code == "not_connected"


async def test_a_transient_refresh_failure_leaves_the_connection_alone(
    sessions: SessionWorld, team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    await connect(team, google)
    await sessions.q(
        "UPDATE booking_calendar_connections SET token_expires_at = now() - interval '1 minute'"
    )
    google.refresh_error = "provider_unreachable"
    service: CalendarConnections = sessions.app.state.calendar_connections
    with pytest.raises(CalendarError) as caught:
        await service.access(uuid.UUID(sessions.tenants[0]), uuid.UUID(person["id"]))
    assert caught.value.code == "provider_unreachable"
    assert (await row(team))["status"] == "connected"


async def test_a_reconnect_after_a_refused_token_makes_it_connected_again(
    team: Team, person: dict[str, Any], google: FakeCalendar
) -> None:
    await connect(team, google)
    await team.w.q("UPDATE booking_calendar_connections SET status = 'needs_reconnect'")
    await connect(team, google, "c2")
    assert (await row(team))["status"] == "connected"
