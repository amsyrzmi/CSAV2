"""Minimal Google Calendar REST and OAuth client for a person's own Bookings calendar.

The one scope is `calendar.app.created`: the app can create secondary calendars and manage the
events on them, and nothing else in the account. Events never carry attendees and never ask Google
to send invitations. Errors carry a code only, never a token, a title or an event id.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from pydantic import SecretStr

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
MAX_JSON_BYTES = 1_048_576
TIMEOUT_SECONDS = 20.0
# Google answers a quota limit with 403 and one of these reasons: a rate limit, not lost access.
RATE_LIMIT_REASONS = frozenset(
    {"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded", "quotaExceeded"}
)


class CalendarError(Exception):
    """A provider failure with a code that is safe to log and to show an operator."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TokenSet:
    access_token: SecretStr
    refresh_token: SecretStr | None
    expires_in: int
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class EventResult:
    """The event Google made or changed, and its Meet link when it has one yet."""

    event_id: str
    meet_link: str | None


def _body(response: httpx.Response) -> Any:
    """The decoded JSON of an answer, or None when it has none; the body is never logged."""
    if len(response.content) > MAX_JSON_BYTES:
        raise CalendarError("provider_response_too_large")
    try:
        return response.json()
    except ValueError:
        return None


def _rate_limited(response: httpx.Response) -> bool:
    """A 403 that is a quota limit. The body is read for its reason only."""
    if len(response.content) > MAX_JSON_BYTES:
        return False
    data = _body(response)
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return False
    reasons = error.get("errors")
    if not isinstance(reasons, list):
        return False
    return any(isinstance(r, dict) and r.get("reason") in RATE_LIMIT_REASONS for r in reasons)


def _failure(response: httpx.Response) -> CalendarError:
    status = response.status_code
    if status == 429 or (status == 403 and _rate_limited(response)):
        return CalendarError("provider_rate_limited")
    if status in {401, 403}:
        return CalendarError("provider_unauthorized")
    if status in {404, 410}:
        return CalendarError("provider_not_found")
    return CalendarError("provider_rejected")


def _object(response: httpx.Response) -> dict[str, Any]:
    data = _body(response)
    if not isinstance(data, dict):
        raise CalendarError("provider_invalid_response")
    return data


def _meet_link(data: dict[str, Any]) -> str | None:
    """The Meet link in an event answer: `hangoutLink`, else a video entry point."""
    link = data.get("hangoutLink")
    if not isinstance(link, str) or not link:
        link = None
        conference = data.get("conferenceData")
        entries = conference.get("entryPoints") if isinstance(conference, dict) else None
        for entry in entries if isinstance(entries, list) else []:
            uri = entry.get("uri") if isinstance(entry, dict) else None
            if isinstance(entry, dict) and entry.get("entryPointType") == "video" and uri:
                link = uri
                break
    return link if isinstance(link, str) and link.startswith("https://") else None


class CalendarClient:
    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
        http: httpx.AsyncClient,
    ) -> None:
        if not client_id or not client_secret.get_secret_value() or not redirect_uri:
            raise ValueError("Calendar client requires client id, client secret and redirect URI")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": CALENDAR_SCOPE,
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTH_ENDPOINT}?{query}"

    async def _send(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._http.send(request, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise CalendarError("provider_unreachable") from exc

    def _form(self, url: str, form: dict[str, str], *, with_client: bool = True) -> httpx.Request:
        data = dict(form)
        if with_client:
            data["client_id"] = self._client_id
            data["client_secret"] = self._client_secret.get_secret_value()
        return httpx.Request(
            "POST",
            url,
            data=data,
            headers={"accept": "application/json"},
            extensions={"timeout": httpx.Timeout(TIMEOUT_SECONDS).as_dict()},
        )

    async def _token(self, form: dict[str, str]) -> TokenSet:
        response = await self._send(self._form(TOKEN_ENDPOINT, form))
        if response.status_code in {400, 401}:
            data = _body(response)
            if isinstance(data, dict) and data.get("error") == "invalid_grant":
                raise CalendarError("invalid_grant")
        if response.status_code == 429:
            raise CalendarError("provider_rate_limited")
        if response.status_code != 200:
            raise CalendarError("provider_rejected")
        data = _object(response)
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise CalendarError("provider_invalid_response")
        refresh = data.get("refresh_token")
        expires = data.get("expires_in", 3600)
        return TokenSet(
            access_token=SecretStr(access),
            refresh_token=SecretStr(refresh) if isinstance(refresh, str) and refresh else None,
            expires_in=int(expires) if isinstance(expires, int | float) else 3600,
            scopes=tuple(str(data.get("scope", "")).split()),
        )

    async def exchange(self, *, code: str, code_verifier: str) -> TokenSet:
        tokens = await self._token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": self._redirect_uri,
            }
        )
        if CALENDAR_SCOPE not in tokens.scopes:
            raise CalendarError("scopes_not_granted")
        return tokens

    async def refresh(self, refresh_token: SecretStr) -> TokenSet:
        return await self._token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token.get_secret_value()}
        )

    async def revoke(self, token: SecretStr) -> None:
        request = self._form(
            REVOKE_ENDPOINT, {"token": token.get_secret_value()}, with_client=False
        )
        response = await self._send(request)
        if response.status_code != 200:
            raise CalendarError("provider_rejected")

    def _api(
        self,
        access: SecretStr,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> httpx.Request:
        url = f"{CALENDAR_API}/{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return httpx.Request(
            method,
            url,
            json=body,
            headers={
                "authorization": f"Bearer {access.get_secret_value()}",
                "accept": "application/json",
            },
            extensions={"timeout": httpx.Timeout(TIMEOUT_SECONDS).as_dict()},
        )

    async def create_calendar(self, access: SecretStr, *, summary: str, time_zone: str) -> str:
        body = {"summary": summary, "timeZone": time_zone}
        response = await self._send(self._api(access, "POST", "calendars", body=body))
        if response.status_code != 200:
            raise _failure(response)
        calendar_id = _object(response).get("id")
        if not isinstance(calendar_id, str) or not calendar_id:
            raise CalendarError("provider_invalid_response")
        return calendar_id

    async def calendar_exists(self, access: SecretStr, calendar_id: str) -> bool:
        path = f"calendars/{quote(calendar_id, safe='')}"
        response = await self._send(self._api(access, "GET", path))
        if response.status_code == 200:
            return True
        if response.status_code in {404, 410}:
            return False
        raise _failure(response)

    @staticmethod
    def _checked(body: dict[str, Any]) -> dict[str, Any]:
        if "attendees" in body:
            raise ValueError("events carry no attendees")
        return dict(body)

    async def insert_event(
        self, access: SecretStr, calendar_id: str, body: dict[str, Any], *, meet: bool = False
    ) -> EventResult:
        payload = self._checked(body)
        params = {"sendUpdates": "none"}
        if meet:
            params["conferenceDataVersion"] = "1"
            payload["conferenceData"] = {
                "createRequest": {
                    "requestId": uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            }
        path = f"calendars/{quote(calendar_id, safe='')}/events"
        response = await self._send(self._api(access, "POST", path, params=params, body=payload))
        if meet and response.status_code == 400:
            # A 400 refuses the whole request: no event was made, so the caller may try without.
            raise CalendarError("meet_unavailable")
        if response.status_code != 200:
            raise _failure(response)
        data = _object(response)
        event_id = data.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise CalendarError("provider_invalid_response")
        return EventResult(event_id, _meet_link(data))

    async def patch_event(
        self, access: SecretStr, calendar_id: str, event_id: str, body: dict[str, Any]
    ) -> None:
        path = f"calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        request = self._api(
            access, "PATCH", path, params={"sendUpdates": "none"}, body=self._checked(body)
        )
        response = await self._send(request)
        if response.status_code != 200:
            raise _failure(response)

    async def delete_event(self, access: SecretStr, calendar_id: str, event_id: str) -> None:
        path = f"calendars/{quote(calendar_id, safe='')}/events/{quote(event_id, safe='')}"
        response = await self._send(
            self._api(access, "DELETE", path, params={"sendUpdates": "none"})
        )
        if response.status_code not in {200, 204}:
            raise _failure(response)
