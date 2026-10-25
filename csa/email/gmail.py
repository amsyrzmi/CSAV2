"""Minimal Gmail REST and Google OAuth client with bounded responses.

Scopes are read-only plus compose: the client can list and read threads and
create drafts in the connecting user's own mailbox. There is deliberately no
send method. Errors never carry tokens or message content.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
)
MAX_JSON_BYTES = 1_048_576
MAX_BODY_CHARS = 20_000
TIMEOUT_SECONDS = 20.0


class GmailError(Exception):
    """Content-free provider failure; the code is safe to show operators."""

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
class FetchedThread:
    thread_id: str
    subject: str
    sender: str
    snippet: str
    latest_message_id: str | None
    body: str | None
    received_at: datetime | None
    labels: tuple[str, ...]
    unread: bool


def pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) per RFC 7636 S256."""
    verifier = secrets.token_urlsafe(64)[:96]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _b64url_decode(value: str) -> bytes:
    padded = value + "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def _extract_body(payload: dict[str, Any]) -> str | None:
    """Prefer text/plain; fall back to stripped text/html. Depth-first over parts."""
    plain: list[str] = []
    rich: list[str] = []

    def walk(part: dict[str, Any], depth: int) -> None:
        if depth > 8:
            return
        mime = str(part.get("mimeType", ""))
        data = part.get("body", {}).get("data")
        if isinstance(data, str) and data:
            try:
                decoded = _b64url_decode(data).decode("utf-8", errors="replace")
            except (ValueError, UnicodeDecodeError):
                decoded = ""
            if mime == "text/plain":
                plain.append(decoded)
            elif mime == "text/html":
                rich.append(_strip_html(decoded))
        for child in part.get("parts", []) or []:
            if isinstance(child, dict):
                walk(child, depth + 1)

    walk(payload, 0)
    text = "\n".join(plain) if plain else "\n".join(rich)
    text = text.replace("\x00", "").strip()
    if not text:
        return None
    return text[:MAX_BODY_CHARS]


def _header(headers: list[dict[str, Any]], name: str) -> str:
    for item in headers:
        if str(item.get("name", "")).lower() == name.lower():
            return str(item.get("value", ""))
    return ""


def build_reply_mime(*, to: str, subject: str, body: str) -> bytes:
    """Plain-text reply; headers already validated by the draft contract."""
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return message.as_bytes()


class GmailClient:
    def __init__(
        self,
        client_id: str,
        client_secret: SecretStr,
        redirect_uri: str,
        http: httpx.AsyncClient,
    ) -> None:
        if not client_id or not client_secret.get_secret_value() or not redirect_uri:
            raise ValueError("Gmail client requires client id, client secret and redirect URI")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = http

    @property
    def redirect_uri(self) -> str:
        return self._redirect_uri

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        query = urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "false",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{AUTH_ENDPOINT}?{query}"

    async def _json(self, request: httpx.Request, *, expected: int = 200) -> dict[str, Any]:
        try:
            response = await self._http.send(request, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise GmailError("provider_unreachable") from exc
        if response.status_code in {401, 403}:
            raise GmailError("provider_unauthorized")
        if response.status_code == 429:
            raise GmailError("provider_rate_limited")
        if response.status_code != expected:
            raise GmailError("provider_rejected")
        if len(response.content) > MAX_JSON_BYTES:
            raise GmailError("provider_response_too_large")
        try:
            data = response.json()
        except ValueError as exc:
            raise GmailError("provider_invalid_response") from exc
        if not isinstance(data, dict):
            raise GmailError("provider_invalid_response")
        return data

    def _token_request(self, form: dict[str, str]) -> httpx.Request:
        return httpx.Request(
            "POST",
            TOKEN_ENDPOINT,
            data={
                **form,
                "client_id": self._client_id,
                "client_secret": self._client_secret.get_secret_value(),
            },
            headers={"accept": "application/json"},
            extensions={"timeout": httpx.Timeout(TIMEOUT_SECONDS).as_dict()},
        )

    @staticmethod
    def _token_set(data: dict[str, Any]) -> TokenSet:
        access = data.get("access_token")
        if not isinstance(access, str) or not access:
            raise GmailError("provider_invalid_response")
        refresh = data.get("refresh_token")
        expires = data.get("expires_in", 3600)
        scopes = tuple(str(data.get("scope", "")).split())
        return TokenSet(
            access_token=SecretStr(access),
            refresh_token=SecretStr(refresh) if isinstance(refresh, str) and refresh else None,
            expires_in=int(expires) if isinstance(expires, int | float) else 3600,
            scopes=scopes,
        )

    async def exchange(self, *, code: str, code_verifier: str) -> TokenSet:
        data = await self._json(
            self._token_request(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": code_verifier,
                    "redirect_uri": self._redirect_uri,
                }
            )
        )
        token = self._token_set(data)
        if not set(SCOPES).issubset(token.scopes):
            raise GmailError("scopes_not_granted")
        return token

    async def refresh(self, refresh_token: SecretStr) -> TokenSet:
        data = await self._json(
            self._token_request(
                {"grant_type": "refresh_token", "refresh_token": refresh_token.get_secret_value()}
            )
        )
        return self._token_set(data)

    def _api(self, access: SecretStr, method: str, path: str, **kwargs: Any) -> httpx.Request:
        return httpx.Request(
            method,
            f"{GMAIL_API}/{path}",
            headers={
                "authorization": f"Bearer {access.get_secret_value()}",
                "accept": "application/json",
            },
            extensions={"timeout": httpx.Timeout(TIMEOUT_SECONDS).as_dict()},
            **kwargs,
        )

    async def profile_address(self, access: SecretStr) -> str:
        data = await self._json(self._api(access, "GET", "profile"))
        address = data.get("emailAddress")
        if not isinstance(address, str) or "@" not in address:
            raise GmailError("provider_invalid_response")
        return address.lower()

    async def list_thread_ids(self, access: SecretStr, *, query: str, limit: int) -> list[str]:
        data = await self._json(
            self._api(
                access,
                "GET",
                "threads",
                params={"q": query, "maxResults": str(max(1, min(limit, 50)))},
            )
        )
        items = data.get("threads", []) or []
        return [str(item["id"]) for item in items if isinstance(item, dict) and item.get("id")]

    async def fetch_thread(self, access: SecretStr, thread_id: str) -> FetchedThread:
        data = await self._json(
            self._api(access, "GET", f"threads/{thread_id}", params={"format": "full"})
        )
        messages = [m for m in data.get("messages", []) or [] if isinstance(m, dict)]
        if not messages:
            raise GmailError("thread_empty")
        latest = messages[-1]
        payload = latest.get("payload", {}) or {}
        headers = payload.get("headers", []) or []
        received: datetime | None = None
        raw_date = _header(headers, "Date")
        if raw_date:
            try:
                received = parsedate_to_datetime(raw_date)
                if received.tzinfo is None:
                    received = received.replace(tzinfo=UTC)
            except (TypeError, ValueError):
                received = None
        if received is None and str(latest.get("internalDate", "")).isdigit():
            received = datetime.fromtimestamp(int(latest["internalDate"]) / 1000, tz=UTC)
        labels = tuple(str(label) for label in latest.get("labelIds", []) or [])
        return FetchedThread(
            thread_id=str(data.get("id", thread_id)),
            subject=_header(headers, "Subject")[:500],
            sender=_header(headers, "From")[:500],
            snippet=html.unescape(str(latest.get("snippet", "")))[:1000],
            latest_message_id=str(latest.get("id")) if latest.get("id") else None,
            body=_extract_body(payload),
            received_at=received,
            labels=labels,
            unread="UNREAD" in labels,
        )

    async def create_draft(self, access: SecretStr, *, thread_id: str | None, mime: bytes) -> str:
        """Create a Gmail draft in the user's mailbox. Never sends."""
        raw = base64.urlsafe_b64encode(mime).decode("ascii")
        message: dict[str, Any] = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        request = self._api(
            access,
            "POST",
            "drafts",
            content=json.dumps({"message": message}).encode("utf-8"),
        )
        request.headers["content-type"] = "application/json"
        data = await self._json(request)
        draft_id = data.get("id")
        if not isinstance(draft_id, str) or not draft_id:
            raise GmailError("provider_invalid_response")
        return draft_id
