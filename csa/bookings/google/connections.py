"""A person's connection to their own Google Calendar: starting it, finishing it, the encrypted
grant, the access token, and disconnecting.

Every database step is its own short transaction and every call to Google is outside one. The
grant is encrypted with `csa.core.crypto`, bound to its row and column. Logs carry categories only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import structlog
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from csa.bookings.google.client import CalendarError, TokenSet
from csa.bookings.google.contracts import CalendarPersonOut, ConnectionState
from csa.core import crypto, db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

TABLE = "booking_calendar_connections"
STATE_TTL_SECONDS = 600
# A token this close to running out is refreshed before it is used.
REFRESH_MARGIN = timedelta(seconds=60)


class CalendarApi(Protocol):
    """The part of `CalendarClient` the connection needs."""

    def authorization_url(self, *, state: str, code_challenge: str) -> str: ...

    async def exchange(self, *, code: str, code_verifier: str) -> TokenSet: ...

    async def refresh(self, refresh_token: SecretStr) -> TokenSet: ...

    async def revoke(self, token: SecretStr) -> None: ...

    async def create_calendar(self, access: SecretStr, *, summary: str, time_zone: str) -> str: ...

    async def calendar_exists(self, access: SecretStr, calendar_id: str) -> bool: ...


@dataclass(frozen=True)
class CalendarAccess:
    """What a push needs to act as one person: a fresh token and their Bookings calendar."""

    token: SecretStr
    calendar_id: str
    connection_id: UUID
    refreshed: bool = False  # the token was refreshed for this call


def pkce_pair() -> tuple[str, str]:
    """A code verifier and its S256 challenge."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _state(raw: object) -> ConnectionState:
    return (
        "connected"
        if raw == "connected"
        else "needs_reconnect"
        if raw == "needs_reconnect"
        else "not_connected"
    )


def _aad(connection_id: UUID, column: str) -> bytes:
    return crypto.credential_aad(TABLE, column, str(connection_id))


class CalendarConnections:
    def __init__(
        self,
        pool: AsyncConnectionPool,
        client: CalendarApi | None,
        *,
        enabled: bool,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.pool = pool
        self.client = client
        self.enabled = enabled
        self.now = now
        # One calendar is made at a time for a connection, so syncs that find it missing together
        # make it once (across processes the stored id decides, see `renew_calendar`).
        self._renewing: dict[UUID, asyncio.Lock] = {}

    @property
    def available(self) -> bool:
        """On, and the Google client is set up."""
        return self.enabled and self.client is not None

    def _api(self) -> CalendarApi:
        if not self.available or self.client is None:
            raise CalendarError("calendar_unavailable")
        return self.client

    async def person_for(self, conn: Any, client_id: UUID, user_id: UUID) -> UUID | None:
        """The active bookable person linked to this account, if any."""
        row = await db.q.fetch_one(
            "bookings/google_person_for_user.sql",
            {"client_id": client_id, "user_id": user_id},
            conn=conn,
        )
        return None if row is None else row["id"]  # type: ignore[no-any-return]

    async def status(self, conn: Any, client_id: UUID, user_id: UUID) -> ConnectionState:
        row = await db.q.fetch_one(
            "bookings/google_connection_by_user.sql",
            {"client_id": client_id, "user_id": user_id},
            conn=conn,
        )
        return _state(row["status"] if row else None)

    async def people(self, conn: Any, client_id: UUID) -> list[CalendarPersonOut]:
        rows = await db.q.fetch_all(
            "bookings/google_people_status.sql", {"client_id": client_id}, conn=conn
        )
        return [
            CalendarPersonOut(person_id=r["person_id"], name=r["name"], status=_state(r["status"]))
            for r in rows
        ]

    async def start(self, client_id: UUID, user_id: UUID) -> str:
        """The Google authorization URL for this member, and a pending connection of their own."""
        api = self._api()
        state = secrets.token_urlsafe(32)
        verifier, challenge = pkce_pair()
        async with tx(self.pool, str(client_id)) as conn:
            person = await self.person_for(conn, client_id, user_id)
            if person is None:
                raise CalendarError("person_required")
            await db.q.execute(
                "bookings/google_connection_release_person.sql",
                {"client_id": client_id, "user_id": user_id, "person_id": person},
                conn=conn,
            )
            row = await db.q.fetch_one(
                "bookings/google_connection_pending_upsert.sql",
                {"client_id": client_id, "user_id": user_id, "person_id": person},
                conn=conn,
            )
            ids = {"client_id": client_id, "user_id": user_id}
            await db.q.execute("bookings/google_state_expire.sql", ids, conn=conn)
            await db.q.execute(
                "bookings/google_state_insert.sql",
                {
                    **ids,
                    "state_hash": digest(state),
                    "connection_id": row["id"],  # type: ignore[index]
                    "code_verifier": verifier,
                    "ttl_seconds": STATE_TTL_SECONDS,
                },
                conn=conn,
            )
        return api.authorization_url(state=state, code_challenge=challenge)

    async def finish(self, client_id: UUID, user_id: UUID, *, code: str, state: str) -> None:
        """The callback: use the state once, exchange the code, find or make the Bookings
        calendar and store the grant encrypted. The person's dropped rows are out of date again.
        """
        api = self._api()
        if not code or len(code) > 2048 or not state or len(state) > 256:
            raise CalendarError("oauth_state_invalid")
        ids = {"client_id": client_id, "user_id": user_id}
        async with tx(self.pool, str(client_id)) as conn:
            consumed = await db.q.fetch_one(
                "bookings/google_state_consume.sql", {**ids, "state_hash": digest(state)}, conn=conn
            )
        if consumed is None:
            raise CalendarError("oauth_state_invalid")
        tokens = await api.exchange(code=code, code_verifier=consumed["code_verifier"])
        async with tx(self.pool, str(client_id)) as conn:
            row = await db.q.fetch_one("bookings/google_connection_by_user.sql", ids, conn=conn)
            facts = await db.q.fetch_one(
                "bookings/mail_facts.sql", {"client_id": client_id}, conn=conn
            )
        if row is None or facts is None or row["id"] != consumed["connection_id"]:
            raise CalendarError("oauth_state_invalid")
        calendar_id: str | None = row["calendar_id"]
        previous = calendar_id
        if calendar_id is None or not await api.calendar_exists(tokens.access_token, calendar_id):
            calendar_id = await api.create_calendar(
                tokens.access_token,
                summary=f"{facts['brand']} Bookings",
                time_zone=facts["timezone"],
            )
        connection_id: UUID = row["id"]
        expires = self.now() + timedelta(seconds=max(60, tokens.expires_in))
        async with tx(self.pool, str(client_id)) as conn:
            done = await db.q.fetch_one(
                "bookings/google_connection_connect.sql",
                {
                    **ids,
                    "id": connection_id,
                    "access_token": crypto.encrypt(
                        tokens.access_token.get_secret_value(), _aad(connection_id, "access_token")
                    ),
                    "refresh_token": crypto.encrypt(
                        tokens.refresh_token.get_secret_value(),
                        _aad(connection_id, "refresh_token"),
                    )
                    if tokens.refresh_token
                    else None,
                    "expires_at": expires,
                    "scopes": list(tokens.scopes),
                    "calendar_id": calendar_id,
                },
                conn=conn,
            )
            if done is None:
                raise CalendarError("oauth_state_invalid")
            person = {"client_id": client_id, "person_id": row["person_id"]}
            await db.q.execute("bookings/google_events_resume.sql", person, conn=conn)
            if previous is not None and calendar_id != previous:
                await db.q.execute("bookings/google_events_repush.sql", person, conn=conn)

    async def disconnect(self, client_id: UUID, user_id: UUID) -> None:
        """Clear the grant and drop the person's open rows at once, then revoke at Google as well
        as it will. The calendar and its events stay in the person's account.
        """
        async with tx(self.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "bookings/google_connection_disconnect.sql",
                {"client_id": client_id, "user_id": user_id},
                conn=conn,
            )
            if row is None:
                raise CalendarError("not_connected")
            await db.q.execute(
                "bookings/google_events_drop.sql",
                {"client_id": client_id, "person_id": row["person_id"]},
                conn=conn,
            )
        if self.client is None:
            return
        for column in ("refresh_token", "access_token"):
            sealed = row[f"old_{column}"]
            if not sealed:
                continue
            try:
                plain = crypto.decrypt(sealed, _aad(row["id"], column))
                await self.client.revoke(SecretStr(plain))
            except Exception:
                logger.warning("booking_calendar_revoke_failed", category="revoke")
            return

    async def access(self, client_id: UUID, person_id: UUID) -> CalendarAccess:
        """A usable token and calendar for one person, refreshed first if it is due. A refused
        refresh token makes the connection `needs_reconnect`; nothing is tried until they connect.
        """
        async with tx(self.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "bookings/google_connection_by_person.sql",
                {"client_id": client_id, "person_id": person_id},
                conn=conn,
            )
        if (
            row is None
            or row["status"] != "connected"
            or not row["calendar_id"]
            or not row["access_token"]
        ):
            raise CalendarError("not_connected")
        connection_id: UUID = row["id"]
        expires: datetime | None = row["token_expires_at"]
        if expires is not None and expires > self.now() + REFRESH_MARGIN:
            token = crypto.decrypt(row["access_token"], _aad(connection_id, "access_token"))
            return CalendarAccess(SecretStr(token), row["calendar_id"], connection_id)
        api = self._api()
        try:
            if not row["refresh_token"]:
                raise CalendarError("invalid_grant")
            sealed = crypto.decrypt(row["refresh_token"], _aad(connection_id, "refresh_token"))
            fresh = await api.refresh(SecretStr(sealed))
        except CalendarError as exc:
            if exc.code == "invalid_grant":
                async with tx(self.pool, str(client_id)) as conn:
                    await db.q.execute(
                        "bookings/google_connection_needs_reconnect.sql",
                        {"client_id": client_id, "id": connection_id},
                        conn=conn,
                    )
                logger.warning("booking_calendar_grant_refused", category="invalid_grant")
            raise
        expiry = self.now() + timedelta(seconds=max(60, fresh.expires_in))
        async with tx(self.pool, str(client_id)) as conn:
            await db.q.execute(
                "bookings/google_connection_refreshed.sql",
                {
                    "client_id": client_id,
                    "id": connection_id,
                    "access_token": crypto.encrypt(
                        fresh.access_token.get_secret_value(), _aad(connection_id, "access_token")
                    ),
                    "expires_at": expiry,
                },
                conn=conn,
            )
        return CalendarAccess(fresh.access_token, row["calendar_id"], connection_id, refreshed=True)

    async def expire_token(self, client_id: UUID, person_id: UUID) -> None:
        """Google refused the access token: refresh it before the next call."""
        async with tx(self.pool, str(client_id)) as conn:
            await db.q.execute(
                "bookings/google_connection_token_expire.sql",
                {"client_id": client_id, "person_id": person_id},
                conn=conn,
            )

    async def renew_calendar(
        self, client_id: UUID, person_id: UUID, stale: str | None = None
    ) -> CalendarAccess:
        """Make the person's Bookings calendar again, after they deleted it in Google, and store
        it. `stale` is the calendar that was found missing: if the stored one is another, someone
        made it first and that one is used. Their events went with the old calendar, so every row
        that said an event was there is pushed again by the job.
        """
        key = (await self._connection_id(client_id, person_id)) or person_id
        lock = self._renewing.setdefault(key, asyncio.Lock())
        async with lock:
            access = await self.access(client_id, person_id)
            if stale is not None and access.calendar_id != stale:
                return access
            api = self._api()
            async with tx(self.pool, str(client_id)) as conn:
                facts = await db.q.fetch_one(
                    "bookings/mail_facts.sql", {"client_id": client_id}, conn=conn
                )
            if facts is None:
                raise CalendarError("not_connected")
            calendar_id = await api.create_calendar(
                access.token, summary=f"{facts['brand']} Bookings", time_zone=facts["timezone"]
            )
            async with tx(self.pool, str(client_id)) as conn:
                done = await db.q.fetch_one(
                    "bookings/google_connection_calendar_set.sql",
                    {
                        "client_id": client_id,
                        "id": access.connection_id,
                        "calendar_id": calendar_id,
                        "old_calendar_id": access.calendar_id,
                    },
                    conn=conn,
                )
                if done is not None:
                    await db.q.execute(
                        "bookings/google_events_repush.sql",
                        {"client_id": client_id, "person_id": person_id},
                        conn=conn,
                    )
            if done is None:
                # Another process stored a calendar first: use it, ours stays empty.
                return await self.access(client_id, person_id)
            return CalendarAccess(access.token, calendar_id, access.connection_id)

    async def _connection_id(self, client_id: UUID, person_id: UUID) -> UUID | None:
        async with tx(self.pool, str(client_id)) as conn:
            row = await db.q.fetch_one(
                "bookings/google_connection_by_person.sql",
                {"client_id": client_id, "person_id": person_id},
                conn=conn,
            )
        return None if row is None else row["id"]  # type: ignore[no-any-return]
