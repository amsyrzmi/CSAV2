"""The Google Calendar client: one scope, no attendees, plain errors, never a token in a message.

Every test uses a mock transport. Nothing here reaches Google.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from csa.bookings.google.client import (
    CALENDAR_SCOPE,
    CalendarClient,
    CalendarError,
    EventResult,
)
from pydantic import SecretStr

ACCESS = SecretStr("synthetic-access-token")
REFRESH = SecretStr("synthetic-refresh-token")
CALENDAR = "abc123@group.calendar.test"
Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> tuple[CalendarClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(record))
    client = CalendarClient(
        "synthetic-client-id",
        SecretStr("synthetic-client-secret"),
        "https://console.example.test/console/v1/bookings/calendar/callback",
        http,
    )
    return client, seen


def _answer(status: int = 200, body: object | None = None) -> Handler:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body if body is not None else {})

    return handler


def _json(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)


def test_the_authorization_url_asks_for_the_one_scope_with_pkce() -> None:
    client, _ = _client(_answer())
    url = urlsplit(client.authorization_url(state="state-1", code_challenge="challenge-1"))
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert (
        f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert (
        query["scope"] == CALENDAR_SCOPE == "https://www.googleapis.com/auth/calendar.app.created"
    )
    assert query["client_id"] == "synthetic-client-id"
    assert query["redirect_uri"].endswith("/console/v1/bookings/calendar/callback")
    assert query["response_type"] == "code"
    assert query["access_type"] == "offline"
    assert query["prompt"] == "consent"
    assert query["include_granted_scopes"] == "false"
    assert query["code_challenge_method"] == "S256"
    assert (query["state"], query["code_challenge"]) == ("state-1", "challenge-1")


async def test_exchange_sends_the_verifier_and_returns_the_tokens() -> None:
    granted = {
        "access_token": "a1",
        "refresh_token": "r1",
        "expires_in": 3599,
        "scope": f"{CALENDAR_SCOPE}",
    }
    client, seen = _client(_answer(200, granted))
    tokens = await client.exchange(code="the-code", code_verifier="v" * 43)
    assert tokens.access_token.get_secret_value() == "a1"
    assert tokens.refresh_token is not None and tokens.refresh_token.get_secret_value() == "r1"
    assert tokens.expires_in == 3599 and tokens.scopes == (CALENDAR_SCOPE,)
    form = parse_qs(seen[0].content.decode())
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == ["the-code"] and form["code_verifier"] == ["v" * 43]


async def test_exchange_refuses_a_grant_without_the_calendar_scope() -> None:
    other = {"access_token": "a1", "refresh_token": "r1", "scope": "openid email"}
    client, _ = _client(_answer(200, other))
    with pytest.raises(CalendarError) as caught:
        await client.exchange(code="c", code_verifier="v" * 43)
    assert caught.value.code == "scopes_not_granted"


async def test_refresh_returns_a_new_access_token() -> None:
    client, seen = _client(_answer(200, {"access_token": "a2", "expires_in": 3000}))
    tokens = await client.refresh(REFRESH)
    assert tokens.access_token.get_secret_value() == "a2" and tokens.refresh_token is None
    assert parse_qs(seen[0].content.decode())["grant_type"] == ["refresh_token"]


@pytest.mark.parametrize("status", [400, 401])
async def test_a_refused_refresh_token_is_invalid_grant(status: int) -> None:
    client, _ = _client(_answer(status, {"error": "invalid_grant"}))
    with pytest.raises(CalendarError) as caught:
        await client.refresh(REFRESH)
    assert caught.value.code == "invalid_grant"


async def test_any_other_token_refusal_is_a_plain_rejection() -> None:
    client, _ = _client(_answer(400, {"error": "invalid_request"}))
    with pytest.raises(CalendarError) as caught:
        await client.refresh(REFRESH)
    assert caught.value.code == "provider_rejected"


RATE_LIMITED = {"error": {"errors": [{"reason": "rateLimitExceeded"}]}}
STATUS_CODES = [
    (401, None, "provider_unauthorized"),
    (403, {"error": {"errors": [{"reason": "forbidden"}]}}, "provider_unauthorized"),
    (403, RATE_LIMITED, "provider_rate_limited"),
    (429, None, "provider_rate_limited"),
    (404, None, "provider_not_found"),
    (410, None, "provider_not_found"),
    (500, None, "provider_rejected"),
]


@pytest.mark.parametrize(("status", "body", "code"), STATUS_CODES)
async def test_every_call_maps_a_failure_to_one_plain_code(
    status: int, body: object, code: str
) -> None:
    client, _ = _client(_answer(status, body))
    calls = (
        client.create_calendar(ACCESS, summary="Bookings", time_zone="Asia/Kuala_Lumpur"),
        client.insert_event(ACCESS, CALENDAR, {"summary": "x"}),
        client.patch_event(ACCESS, CALENDAR, "evt-1", {"summary": "y"}),
        client.delete_event(ACCESS, CALENDAR, "evt-1"),
    )
    for call in calls:
        with pytest.raises(CalendarError) as caught:
            await call
        assert caught.value.code == code
        assert "synthetic-access-token" not in str(caught.value)


async def test_a_timeout_is_provider_unreachable() -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    client, _ = _client(timeout)
    with pytest.raises(CalendarError) as caught:
        await client.insert_event(ACCESS, CALENDAR, {"summary": "x"})
    assert caught.value.code == "provider_unreachable"


async def test_an_oversized_or_unreadable_answer_is_refused() -> None:
    big, _ = _client(lambda _: httpx.Response(200, content=b"x" * 1_100_000))
    with pytest.raises(CalendarError) as caught:
        await big.create_calendar(ACCESS, summary="B", time_zone="UTC")
    assert caught.value.code == "provider_response_too_large"
    junk, _ = _client(lambda _: httpx.Response(200, content=b"not json"))
    with pytest.raises(CalendarError) as caught:
        await junk.create_calendar(ACCESS, summary="B", time_zone="UTC")
    assert caught.value.code == "provider_invalid_response"


async def test_create_calendar_sends_its_name_and_zone_and_returns_the_id() -> None:
    client, seen = _client(_answer(200, {"id": CALENDAR}))
    made = await client.create_calendar(ACCESS, summary="Synthetic Co Bookings", time_zone="UTC")
    assert made == CALENDAR
    assert seen[0].method == "POST" and seen[0].url.path == "/calendar/v3/calendars"
    assert _json(seen[0]) == {"summary": "Synthetic Co Bookings", "timeZone": "UTC"}
    assert seen[0].headers["authorization"] == "Bearer synthetic-access-token"


@pytest.mark.parametrize(("status", "exists"), [(200, True), (404, False), (410, False)])
async def test_calendar_exists_reads_one_calendar(status: int, exists: bool) -> None:
    client, seen = _client(_answer(status, {"id": CALENDAR}))
    assert await client.calendar_exists(ACCESS, CALENDAR) is exists
    assert (
        seen[0].method == "GET" and "abc123%40group.calendar.test" in seen[0].url.raw_path.decode()
    )


async def test_calendar_exists_does_not_hide_a_refused_token() -> None:
    client, _ = _client(_answer(401))
    with pytest.raises(CalendarError) as caught:
        await client.calendar_exists(ACCESS, CALENDAR)
    assert caught.value.code == "provider_unauthorized"


async def test_an_event_goes_in_with_no_attendees_and_asks_for_no_updates() -> None:
    client, seen = _client(_answer(200, {"id": "evt-1"}))
    result = await client.insert_event(ACCESS, CALENDAR, {"summary": "Haircut", "location": "x"})
    assert result == EventResult("evt-1", None)
    request = seen[0]
    assert request.method == "POST"
    assert request.url.raw_path.decode().startswith(
        "/calendar/v3/calendars/abc123%40group.calendar.test/events?"
    )
    assert parse_qs(request.url.query.decode())["sendUpdates"] == ["none"]
    body = _json(request)
    assert body["summary"] == "Haircut" and "attendees" not in body
    assert "conferenceData" not in body


async def test_an_event_with_meet_asks_for_a_conference_and_returns_the_link() -> None:
    answer = {"id": "evt-2", "hangoutLink": "https://meet.example.test/abc-defg-hij"}
    client, seen = _client(_answer(200, answer))
    result = await client.insert_event(ACCESS, CALENDAR, {"summary": "Call"}, meet=True)
    assert result == EventResult("evt-2", "https://meet.example.test/abc-defg-hij")
    query = parse_qs(seen[0].url.query.decode())
    assert query["conferenceDataVersion"] == ["1"] and query["sendUpdates"] == ["none"]
    create = _json(seen[0])["conferenceData"]["createRequest"]  # type: ignore[index]
    assert create["conferenceSolutionKey"] == {"type": "hangoutsMeet"}
    assert create["requestId"]


async def test_a_meet_link_in_the_entry_points_is_found_too() -> None:
    entry = {"entryPointType": "video", "uri": "https://meet.example.test/xyz"}
    answer = {"id": "evt-3", "conferenceData": {"entryPoints": [entry]}}
    client, _ = _client(_answer(200, answer))
    result = await client.insert_event(ACCESS, CALENDAR, {"summary": "Call"}, meet=True)
    assert result == EventResult("evt-3", "https://meet.example.test/xyz")


async def test_an_answer_without_a_link_keeps_the_event_id() -> None:
    """The event exists even if Google has not made the link yet: losing its id would double it."""
    client, _ = _client(_answer(200, {"id": "evt-4", "conferenceData": {"createRequest": {}}}))
    result = await client.insert_event(ACCESS, CALENDAR, {"summary": "Call"}, meet=True)
    assert result == EventResult("evt-4", None)


async def test_a_refused_conference_request_is_meet_unavailable() -> None:
    """A 400 refuses the whole request, so no event was made and the caller may try without."""
    client, _ = _client(_answer(400, {"error": {"message": "Invalid conference type value."}}))
    with pytest.raises(CalendarError) as caught:
        await client.insert_event(ACCESS, CALENDAR, {"summary": "Call"}, meet=True)
    assert caught.value.code == "meet_unavailable"
    plain, _ = _client(_answer(400, {"error": {"message": "Bad Request"}}))
    with pytest.raises(CalendarError) as caught:
        await plain.insert_event(ACCESS, CALENDAR, {"summary": "Call"})
    assert caught.value.code == "provider_rejected"


async def test_patch_and_delete_name_the_event_and_ask_for_no_updates() -> None:
    client, seen = _client(lambda r: httpx.Response(204 if r.method == "DELETE" else 200, json={}))
    await client.patch_event(ACCESS, CALENDAR, "evt-1", {"summary": "New"})
    await client.delete_event(ACCESS, CALENDAR, "evt-1")
    patch, delete = seen
    assert patch.method == "PATCH" and delete.method == "DELETE"
    for request in seen:
        assert "/events/evt-1" in request.url.path
        assert parse_qs(request.url.query.decode())["sendUpdates"] == ["none"]
    assert _json(patch) == {"summary": "New"}


async def test_revoke_posts_the_token_and_a_refusal_is_a_plain_error() -> None:
    client, seen = _client(_answer(200))
    await client.revoke(REFRESH)
    assert seen[0].url.host == "oauth2.googleapis.com" and seen[0].url.path == "/revoke"
    assert parse_qs(seen[0].content.decode())["token"] == ["synthetic-refresh-token"]
    refused, _ = _client(_answer(400, {"error": "invalid_token"}))
    with pytest.raises(CalendarError) as caught:
        await refused.revoke(REFRESH)
    assert caught.value.code == "provider_rejected"
    assert "synthetic-refresh-token" not in str(caught.value) + repr(caught.value)


def test_the_client_needs_its_id_secret_and_redirect() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(_answer()))
    for args in (
        ("", SecretStr("s"), "https://x.test/cb"),
        ("i", SecretStr(""), "https://x.test/cb"),
    ):
        with pytest.raises(ValueError):
            CalendarClient(*args, http)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CalendarClient("i", SecretStr("s"), "", http)
