"""Minimal Gmail REST and Google OAuth client with bounded responses.

Scopes are read-only plus compose: the client can list and read threads and
create drafts or send messages in the connecting user's own mailbox
(`gmail.compose` covers `users.messages.send`; callers must have a confirmed
send card first). Errors never carry tokens or message content.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from pydantic import SecretStr

from csa.agents.staged.models import reject_header_control_chars, validate_mailbox_address

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
MAX_RECIPIENTS = 20
MAX_ADDRESS_CHARS = 254
MAX_SUBJECT_CHARS = 200
MAX_BODY_INPUT_CHARS = 20_000
MAX_MESSAGE_ID_CHARS = 500
MAX_REFERENCES_CHARS = 2000
# Statuses after which the message may still have been delivered: never a clear refusal.
AMBIGUOUS_SEND_STATUSES = frozenset({408, 409, 499})
# Gmail answers a quota limit with 403 and one of these reasons: a rate limit, not lost access.
RATE_LIMIT_REASONS = frozenset({"userRateLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded"})
# users.messages.list pages walked per sync or load-more, and Gmail's page-size ceiling here.
MAX_LIST_PAGES = 3
MAX_LIST_RESULTS = 100


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
    latest_rfc_message_id: str | None = None
    latest_references: str | None = None
    latest_reply_to: str | None = None
    latest_from: str | None = None
    latest_non_owner_from: str | None = None
    latest_non_owner_reply_to: str | None = None


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


def _from_owner(message: dict[str, Any], owner: str) -> bool:
    """Gmail labels everything the mailbox sent, send-as aliases included, `SENT`."""
    if "SENT" in (message.get("labelIds", []) or []):
        return True
    headers = (message.get("payload", {}) or {}).get("headers", []) or []
    return bool(owner) and parseaddr(_header(headers, "From"))[1].strip().lower() == owner


def _received_at(message: dict[str, Any], headers: list[dict[str, Any]]) -> datetime | None:
    """When Gmail received the message (`internalDate`); the sender's Date header only if absent.

    The Date header is set by the sender, so a fallback Date in the future counts as now.
    """
    internal = str(message.get("internalDate", ""))
    if internal.isdigit():
        return datetime.fromtimestamp(int(internal) / 1000, tz=UTC)
    raw_date = _header(headers, "Date")
    if not raw_date:
        return None
    try:
        received = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return None
    if received.tzinfo is None:
        received = received.replace(tzinfo=UTC)
    return min(received, datetime.now(UTC))


def _rate_limited(response: httpx.Response) -> bool:
    """A 403 that is a Gmail quota limit. The body is read for its reason only, never logged."""
    if len(response.content) > MAX_JSON_BYTES:
        return False
    try:
        data = response.json()
    except ValueError:
        return False
    error = data.get("error") if isinstance(data, dict) else None
    if not isinstance(error, dict):
        return False
    reasons = {
        item.get("reason") for item in error.get("errors", []) or [] if isinstance(item, dict)
    }
    reasons.add(error.get("status"))
    return bool(reasons & RATE_LIMIT_REASONS)


def _checked_addresses(values: Sequence[str]) -> list[str]:
    checked: list[str] = []
    for value in values:
        if len(value) > MAX_ADDRESS_CHARS:
            raise ValueError("address too long")
        checked.append(validate_mailbox_address(value))
    return checked


def trim_references(value: str | None, limit: int = MAX_REFERENCES_CHARS) -> str | None:
    """Keep the newest whole Message-IDs of a References chain within `limit` characters."""
    ids = (value or "").split()
    while ids and len(" ".join(ids)) > limit:
        ids.pop(0)
    return " ".join(ids) or None


def _bounded(value: str, limit: int) -> str | None:
    """A header kept whole or not at all: a cut Message-ID would point at nothing."""
    value = value.strip()
    return value if value and len(value) <= limit else None


def build_message_mime(
    *,
    to: Sequence[str],
    cc: Sequence[str] = (),
    bcc: Sequence[str] = (),
    subject: str,
    body: str,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> bytes:
    """Plain-text message. Every address and header value uses the draft contract's rules.

    Bcc is written as a header because that is the only way `users.messages.send` takes it;
    Gmail delivers to it and does not show it to other recipients.
    """
    try:
        to_list, cc_list, bcc_list = (
            _checked_addresses(to),
            _checked_addresses(cc),
            _checked_addresses(bcc),
        )
        if not 1 <= len(to_list) + len(cc_list) + len(bcc_list) <= MAX_RECIPIENTS:
            raise ValueError("one to twenty recipients required")
        if (
            not subject
            or len(subject) > MAX_SUBJECT_CHARS
            or not body.strip()
            or len(body) > MAX_BODY_INPUT_CHARS
            or "\x00" in body
        ):
            raise ValueError("subject or body out of bounds")
        reject_header_control_chars(subject)
        threading = {"In-Reply-To": in_reply_to, "References": references}
        limits = {"In-Reply-To": MAX_MESSAGE_ID_CHARS, "References": MAX_REFERENCES_CHARS}
        for name, value in threading.items():
            if value is not None:
                reject_header_control_chars(value)
                if len(value) > limits[name]:
                    raise ValueError("threading header too long")
    except ValueError as exc:
        raise GmailError("invalid_message") from exc
    message = EmailMessage()
    if to_list:
        message["To"] = ", ".join(to_list)
    if cc_list:
        message["Cc"] = ", ".join(cc_list)
    if bcc_list:
        message["Bcc"] = ", ".join(bcc_list)
    message["Subject"] = subject
    for name, value in threading.items():
        if value:
            message[name] = value
    message.set_content(body)
    return message.as_bytes()


def build_reply_mime(*, to: str, subject: str, body: str) -> bytes:
    """Plain-text single-recipient reply; headers validated by `build_message_mime`."""
    return build_message_mime(to=[to], subject=subject, body=body)


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
        if response.status_code == 429 or (response.status_code == 403 and _rate_limited(response)):
            raise GmailError("provider_rate_limited")
        if response.status_code in {401, 403}:
            raise GmailError("provider_unauthorized")
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

    async def list_recent_thread_ids(
        self, access: SecretStr, *, query: str, limit: int, page_token: str | None = None
    ) -> tuple[list[str], str | None]:
        """Up to ``limit`` thread ids, most recent activity first, and the next page token.

        ``threads.list`` orders by thread creation, so an old conversation with new mail never
        reached the first page. ``messages.list`` is newest message first: the distinct
        ``threadId``s in order of first appearance are the conversations by latest message.
        Each page asks for no more messages than threads still needed, so no page ends past
        the limit and the token returned is exactly where the next load starts. At most
        MAX_LIST_PAGES calls; no token means nothing older is left.
        """
        ids: list[str] = []
        seen: set[str] = set()
        token = page_token
        for _ in range(MAX_LIST_PAGES):
            wanted = max(1, min(limit - len(ids), MAX_LIST_RESULTS))
            params = {"q": query, "maxResults": str(wanted)}
            if token:
                params["pageToken"] = token
            data = await self._json(self._api(access, "GET", "messages", params=params))
            items = data.get("messages", [])
            if not isinstance(items, list):
                raise GmailError("provider_invalid_response")
            for item in items:
                thread_id = item.get("threadId") if isinstance(item, dict) else None
                if isinstance(thread_id, str) and thread_id and thread_id not in seen:
                    seen.add(thread_id)
                    ids.append(thread_id)
            next_token = data.get("nextPageToken")
            token = next_token if isinstance(next_token, str) and next_token else None
            if token is None or len(ids) >= limit:
                break
        return ids, token

    async def fetch_thread(
        self, access: SecretStr, thread_id: str, *, owner_address: str
    ) -> FetchedThread:
        data = await self._json(
            self._api(access, "GET", f"threads/{thread_id}", params={"format": "full"})
        )
        messages = [m for m in data.get("messages", []) or [] if isinstance(m, dict)]
        if not messages:
            raise GmailError("thread_empty")
        latest = messages[-1]
        payload = latest.get("payload", {}) or {}
        headers = payload.get("headers", []) or []
        received = _received_at(latest, headers)
        labels = tuple(str(label) for label in latest.get("labelIds", []) or [])
        owner = owner_address.strip().lower()
        non_owner_headers: list[dict[str, Any]] | None = None
        for message in reversed(messages):
            if not _from_owner(message, owner):
                non_owner_headers = (message.get("payload", {}) or {}).get("headers", []) or []
                break
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
            latest_rfc_message_id=_bounded(_header(headers, "Message-ID"), MAX_MESSAGE_ID_CHARS),
            latest_references=trim_references(_header(headers, "References")),
            latest_reply_to=_header(headers, "Reply-To")[:500] or None,
            latest_from=_header(headers, "From")[:500] or None,
            latest_non_owner_from=(
                _header(non_owner_headers, "From")[:500] or None if non_owner_headers else None
            ),
            latest_non_owner_reply_to=(
                _header(non_owner_headers, "Reply-To")[:500] or None if non_owner_headers else None
            ),
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

    async def send_message(self, access: SecretStr, *, thread_id: str | None, mime: bytes) -> str:
        """Send through `users.messages.send` once; the caller owns at-most-once.

        Clear refusals, where nothing was sent: 401/403 `provider_unauthorized`, 429 or a 403
        quota reason (`userRateLimitExceeded`, `rateLimitExceeded`, `dailyLimitExceeded`)
        `provider_rate_limited`, any other 4xx `send_refused`. A timeout, a connection failure,
        408/409/499 or a 5xx leaves the outcome unknown (`provider_unreachable`), and so does a
        200 without a message id (`provider_invalid_response`).
        """
        message: dict[str, Any] = {"raw": base64.urlsafe_b64encode(mime).decode("ascii")}
        if thread_id:
            message["threadId"] = thread_id
        request = self._api(
            access, "POST", "messages/send", content=json.dumps(message).encode("utf-8")
        )
        request.headers["content-type"] = "application/json"
        try:
            response = await self._http.send(request, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise GmailError("provider_unreachable") from exc
        status = response.status_code
        if status in AMBIGUOUS_SEND_STATUSES:
            raise GmailError("provider_unreachable")
        if status == 429 or (status == 403 and _rate_limited(response)):
            raise GmailError("provider_rate_limited")
        if status in {401, 403}:
            raise GmailError("provider_unauthorized")
        if 400 <= status < 500:
            raise GmailError("send_refused")
        if status != 200:
            raise GmailError("provider_unreachable")
        try:
            data = response.json()
        except ValueError as exc:
            raise GmailError("provider_invalid_response") from exc
        message_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(message_id, str) or not message_id:
            raise GmailError("provider_invalid_response")
        return message_id
