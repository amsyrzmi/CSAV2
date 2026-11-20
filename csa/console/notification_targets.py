"""Where Inbox notifications go: the company's notification targets and a person's own email
preferences (inbox refinement part 3, design 2026-11-19, sections 5.1, 5.3 and 5.4).

Routes, under ``/notifications`` (no collision with the bell routes of
:mod:`csa.console.notifications`: ``""``, ``/count`` and ``/read``):

* ``GET /targets`` lists the Telegram staff chat and the email targets, any member may read. The
  full address is never returned, only ``address_masked`` (``a***@domain``, or the last four
  characters of a chat id). ``email_available`` says whether the relay is on; ``events_offered`` is
  the one list of events for both channels.
* ``PUT /targets/{id}`` replaces a target's settings from the version read (409
  ``version_conflict``), bumps the version and clears the target's failure flag.
* ``POST /targets/email`` sends a test mail first; a refusal is 502 ``email_test_failed`` and
  nothing is saved. A delivered test saves the target enabled and verified. 409
  ``duplicate_address`` and 409 ``too_many_email_targets`` (10 a company).
* ``POST /targets/{id}/test`` sends the test mail again (email targets; Telegram is 400
  ``test_not_supported``) and, delivered, verifies the target and clears its failure flag.
* ``DELETE /targets/{id}`` removes an email target (a Telegram chat is 400: it is created and
  replaced on Channels, never here).
* ``GET`` and ``PUT /preferences``: the signed-in member's own email preferences, any role
  including read-only (the PUT is on the read-only allow list of :mod:`csa.console.access`).

Tenant admins and platform admins write targets; a platform admin names the company with
``client_id`` as everywhere in the console and has no preferences (404). The mail routes that
send (add, test, turning email on) answer 503 ``email_not_configured`` while the relay is off;
listing, editing, removing and turning email off still work. Every write is audited with ids
only, never an address.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import Annotated, Any, Literal, Self
from uuid import UUID
from zoneinfo import available_timezones

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import AfterValidator, Field, StringConstraints, field_validator, model_validator

from csa.console.access import tenant
from csa.console.contracts import ConsoleActor, Contract, Role
from csa.console.kb_uploads import audit
from csa.console.mailer import Mailer, OutgoingMail, one_line
from csa.console.notifications import notification_actor
from csa.core import db
from csa.core.tenancy import tx
from csa.notify.email_render import render_test_email
from csa.notify.failures import clear_target_failure
from csa.notify.prefs import KINDS, Prefs, get_prefs, save_prefs

logger = structlog.get_logger(__name__)

EVENTS_OFFERED: tuple[str, ...] = (
    "escalation",
    "approval_needed",
    "order_submitted",
    "dlq",
    "circuit_opened",
    "booking_pending",
    "inbox.overdue",
)
MAX_EMAIL_TARGETS = 10
MAX_ADDRESS = 254
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
_ADDRESS = re.compile(rf"[^\s@<>,;\"'()\[\]\\]+@{_LABEL}(?:\.{_LABEL})+")

Priority = Literal["low", "normal", "high", "urgent"]
Language = Literal["en", "ms"]
ClockTime = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]


@lru_cache(maxsize=1)
def _zones() -> frozenset[str]:
    return frozenset(available_timezones())


class QuietHours(Contract):
    """A daily window in which only urgent notifications are sent; it may cross midnight."""

    start: ClockTime
    end: ClockTime
    tz: str

    @model_validator(mode="after")
    def _valid(self) -> Self:
        if self.tz not in _zones():
            raise ValueError("tz must be an IANA time zone name")
        if self.start == self.end:
            raise ValueError("start and end must differ")
        return self


def _events(values: list[str]) -> list[str]:
    unknown = [value for value in values if value not in EVENTS_OFFERED]
    if unknown:
        raise ValueError(f"unknown event {unknown[0]!r}")
    return [event for event in EVENTS_OFFERED if event in values]


Events = Annotated[list[str], Field(max_length=len(EVENTS_OFFERED) * 2), AfterValidator(_events)]


def _address(value: str) -> str:
    address = value.strip().lower()
    local = address.partition("@")[0]
    if (
        len(address) > MAX_ADDRESS
        or len(local) > 64
        or not one_line(address)
        or _ADDRESS.fullmatch(address) is None
    ):
        raise ValueError("a valid email address is required")
    return address


class TargetIn(Contract):
    """A target's settings, replaced as a whole. ``version`` is the one last read."""

    events: Events
    min_priority: Priority
    quiet_hours: QuietHours | None
    language: Language
    is_enabled: bool
    version: int = Field(ge=0)


class EmailTargetIn(Contract):
    """A new email target; what is left out takes the defaults of a Telegram chat."""

    address: Annotated[str, Field(max_length=MAX_ADDRESS + 64), AfterValidator(_address)]
    events: Events = Field(default_factory=lambda: list(EVENTS_OFFERED))
    min_priority: Priority = "normal"
    quiet_hours: QuietHours | None = None
    language: Language = "en"


class LastFailure(Contract):
    at: datetime
    attempts: int


class TargetOut(Contract):
    id: UUID
    channel: Literal["telegram", "email"]
    address_masked: str
    events: list[str]
    min_priority: Priority
    quiet_hours: QuietHours | None
    language: Language
    is_enabled: bool
    verified: bool
    version: int
    last_failure: LastFailure | None


class TargetList(Contract):
    targets: list[TargetOut]
    email_available: bool
    events_offered: list[str]


class PreferencesIn(Contract):
    """The person's own email preferences: on or off, which of the six kinds, the priority floor."""

    email_enabled: bool
    kinds: Annotated[list[str], Field(max_length=len(KINDS) * 2)]
    min_priority: Priority

    @field_validator("kinds")
    @classmethod
    def _known_kinds(cls, values: list[str]) -> list[str]:
        unknown = [value for value in values if value not in KINDS]
        if unknown:
            raise ValueError(f"unknown kind {unknown[0]!r}")
        return [kind for kind in KINDS if kind in values]


class PreferencesOut(PreferencesIn):
    """The preferences as stored, or the defaults (email off) while none are saved."""


def mask_address(channel: str, address: str) -> str:
    """``a***@domain`` for email, the last four characters of a Telegram chat id."""
    if channel == "email":
        local, _, domain = address.partition("@")
        return f"{local[:1]}***@{domain}"
    return address[-4:]


def target_admin(actor: Annotated[ConsoleActor, Depends(notification_actor)]) -> ConsoleActor:
    """Targets are changed by a tenant admin or a platform admin; staff and readers get 403."""
    if actor.role not in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN}:
        raise HTTPException(403, "notification targets require an administrator")
    return actor


def preference_member(actor: Annotated[ConsoleActor, Depends(notification_actor)]) -> ConsoleActor:
    """Preferences belong to a member of a company; a platform admin has none (404)."""
    if actor.platform_admin:
        raise HTTPException(404, "a platform admin has no notification preferences")
    return actor


def _relay(request: Request) -> Mailer:
    mailer = getattr(request.app.state, "mailer", None)
    if not isinstance(mailer, Mailer) or not mailer.enabled:
        raise HTTPException(503, "email_not_configured")
    return mailer


def _relay_on(request: Request) -> bool:
    mailer = getattr(request.app.state, "mailer", None)
    return isinstance(mailer, Mailer) and mailer.enabled


def _quiet_hours(stored: Any) -> QuietHours | None:
    """The stored window; one that does not read as a valid window is shown as none."""
    if stored is None:
        return None
    try:
        return QuietHours.model_validate(stored)
    except ValueError:
        logger.warning("notification_target_quiet_hours_unreadable")
        return None


def _present(row: dict[str, Any], failures: dict[UUID, dict[str, Any]]) -> TargetOut:
    failure = failures.get(row["id"])
    return TargetOut(
        id=row["id"],
        channel=row["channel"],
        address_masked=mask_address(row["channel"], row["address"]),
        events=list(row["events"]),
        min_priority=row["min_priority"],
        quiet_hours=_quiet_hours(row["quiet_hours"]),
        language=row["language"],
        is_enabled=row["is_enabled"],
        verified=row["verified"],
        version=row["version"],
        last_failure=(
            None if failure is None else LastFailure(at=failure["at"], attempts=failure["attempts"])
        ),
    )


async def _failures(conn: AsyncConnection[Any], client_id: UUID) -> dict[UUID, dict[str, Any]]:
    rows = await db.q.fetch_all(
        "console/notification_targets_last_failure.sql", {"client_id": client_id}, conn=conn
    )
    return {row["target_id"]: row for row in rows}


async def _one(conn: AsyncConnection[Any], client_id: UUID, target_id: UUID) -> TargetOut:
    row = await _found(conn, client_id, target_id)
    return _present(row, await _failures(conn, client_id))


async def _found(conn: AsyncConnection[Any], client_id: UUID, target_id: UUID) -> dict[str, Any]:
    row = await db.q.fetch_one(
        "console/notification_targets_get.sql",
        {"client_id": client_id, "target_id": target_id},
        conn=conn,
    )
    if row is None:
        raise HTTPException(404, "notification target not found")
    return row  # type: ignore[no-any-return]


async def _lock(conn: AsyncConnection[Any], client_id: UUID) -> None:
    await db.q.fetch_one(
        "inbox/lock_request.sql", (f"notification-targets:{client_id}",), conn=conn
    )


async def _room_for(conn: AsyncConnection[Any], client_id: UUID, address: str) -> None:
    """409 when the address is taken or the company already has the most email targets."""
    row = await db.q.fetch_one(
        "console/notification_targets_count_email.sql",
        {"client_id": client_id, "address": address},
        conn=conn,
    )
    if row["has_address"]:
        raise HTTPException(409, "duplicate_address")
    if row["n"] >= MAX_EMAIL_TARGETS:
        raise HTTPException(409, "too_many_email_targets")


async def _send_test(request: Request, address: str, language: str) -> None:
    """Send the test mail now, outside any transaction; anything but a delivery is a 502."""
    rendered = render_test_email(language)
    outcome = await _relay(request).send(
        OutgoingMail(to=address, subject=rendered.subject, text=rendered.text, html=rendered.html)
    )
    if outcome != "sent":
        raise HTTPException(502, "email_test_failed")


def build_notification_targets_router() -> APIRouter:
    """The target and preference routes under ``/notifications`` (in the console router)."""
    router = APIRouter(prefix="/notifications", tags=["notifications"])

    @router.get("/targets", response_model=TargetList)
    async def list_targets(
        request: Request,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(notification_actor)],
    ) -> TargetList:
        """The company's Telegram chat and email targets; any member may read."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            rows = await db.q.fetch_all(
                "console/notification_targets_list.sql", {"client_id": client_id}, conn=conn
            )
            failures = await _failures(conn, client_id)
        return TargetList(
            targets=[_present(row, failures) for row in rows],
            email_available=_relay_on(request),
            events_offered=list(EVENTS_OFFERED),
        )

    @router.post("/targets/email", response_model=TargetOut, status_code=201)
    async def add_email_target(
        request: Request,
        payload: EmailTargetIn,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(target_admin)],
    ) -> TargetOut:
        """Add an email target after a delivered test mail.

        A relay refusal is 502 `email_test_failed` and nothing is saved.
        """
        _relay(request)
        pool = request.app.state.pool
        async with tx(pool, str(client_id)) as conn:
            await _room_for(conn, client_id, payload.address)
        await _send_test(request, payload.address, payload.language)
        async with tx(pool, str(client_id)) as conn:
            await _lock(conn, client_id)
            await _room_for(conn, client_id, payload.address)
            row = await db.q.fetch_one(
                "console/notification_targets_insert_email.sql",
                {
                    "client_id": client_id,
                    "address": payload.address,
                    "events": payload.events,
                    "min_priority": payload.min_priority,
                    "quiet_hours": _stored_hours(payload.quiet_hours),
                    "language": payload.language,
                },
                conn=conn,
            )
            if row is None:
                raise HTTPException(409, "duplicate_address")
            await audit(conn, actor, client_id, "notifications.target.added", str(row["id"]))
            return await _one(conn, client_id, row["id"])

    @router.put("/targets/{target_id}", response_model=TargetOut)
    async def update_target(
        request: Request,
        target_id: UUID,
        payload: TargetIn,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(target_admin)],
    ) -> TargetOut:
        """Replace a target's settings; 409 `version_conflict` unless `version` is the one read."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            await _found(conn, client_id, target_id)
            updated = await db.q.fetch_one(
                "console/notification_targets_update.sql",
                {
                    "client_id": client_id,
                    "target_id": target_id,
                    "version": payload.version,
                    "events": payload.events,
                    "min_priority": payload.min_priority,
                    "quiet_hours": _stored_hours(payload.quiet_hours),
                    "language": payload.language,
                    "is_enabled": payload.is_enabled,
                },
                conn=conn,
            )
            if updated is None:
                raise HTTPException(409, "version_conflict")
            await clear_target_failure(conn, client_id, target_id)
            await audit(conn, actor, client_id, "notifications.target.updated", str(target_id))
            return await _one(conn, client_id, target_id)

    @router.post("/targets/{target_id}/test", response_model=TargetOut)
    async def test_target(
        request: Request,
        target_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(target_admin)],
    ) -> TargetOut:
        """Send a test mail to an email target.

        Delivered, it verifies the target and clears its failure flag. Telegram is 400.
        """
        pool = request.app.state.pool
        async with tx(pool, str(client_id)) as conn:
            row = await _found(conn, client_id, target_id)
        if row["channel"] != "email":
            raise HTTPException(400, "test_not_supported")
        _relay(request)
        await _send_test(request, row["address"], row["language"])
        async with tx(pool, str(client_id)) as conn:
            verified = await db.q.fetch_one(
                "console/notification_targets_verify.sql",
                {"client_id": client_id, "target_id": target_id},
                conn=conn,
            )
            if verified is None:  # removed while the mail was on its way
                raise HTTPException(404, "notification target not found")
            await clear_target_failure(conn, client_id, target_id)
            await audit(conn, actor, client_id, "notifications.target.tested", str(target_id))
            return await _one(conn, client_id, target_id)

    @router.delete("/targets/{target_id}", status_code=204)
    async def remove_target(
        request: Request,
        target_id: UUID,
        client_id: Annotated[UUID, Depends(tenant)],
        actor: Annotated[ConsoleActor, Depends(target_admin)],
    ) -> Response:
        """Remove an email target; the Telegram chat cannot be removed here (400)."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            row = await _found(conn, client_id, target_id)
            if row["channel"] != "email":
                raise HTTPException(400, "telegram_target_not_removable")
            await db.q.fetch_one(
                "console/notification_targets_delete.sql",
                {"client_id": client_id, "target_id": target_id},
                conn=conn,
            )
            await audit(conn, actor, client_id, "notifications.target.removed", str(target_id))
        return Response(status_code=204)

    @router.get("/preferences", response_model=PreferencesOut)
    async def get_preferences(
        request: Request,
        actor: Annotated[ConsoleActor, Depends(preference_member)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> PreferencesOut:
        """The signed-in member's own email preferences; defaults (email off) until they save."""
        async with tx(request.app.state.pool, str(client_id)) as conn:
            prefs = await get_prefs(conn, client_id, actor.user_id)
        return PreferencesOut(
            email_enabled=prefs.email_enabled,
            kinds=list(prefs.kinds),
            min_priority=prefs.min_priority,  # type: ignore[arg-type]
        )

    @router.put("/preferences", response_model=PreferencesOut)
    async def put_preferences(
        request: Request,
        payload: PreferencesIn,
        actor: Annotated[ConsoleActor, Depends(preference_member)],
        client_id: Annotated[UUID, Depends(tenant)],
    ) -> PreferencesOut:
        """Save the member's own preferences (last write wins).

        Turning email on needs the relay (503 `email_not_configured`), turning it off does not.
        """
        if payload.email_enabled:
            _relay(request)
        async with tx(request.app.state.pool, str(client_id)) as conn:
            saved = await save_prefs(
                conn,
                client_id,
                actor.user_id,
                Prefs(payload.email_enabled, tuple(payload.kinds), payload.min_priority),
            )
            await audit(
                conn, actor, client_id, "notifications.preferences.updated", str(actor.user_id)
            )
        return PreferencesOut(
            email_enabled=saved.email_enabled,
            kinds=list(saved.kinds),
            min_priority=saved.min_priority,  # type: ignore[arg-type]
        )

    return router


def _stored_hours(hours: QuietHours | None) -> Jsonb | None:
    return None if hours is None else Jsonb(hours.model_dump())
