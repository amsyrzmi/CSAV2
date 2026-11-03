"""Reading and moving a company's HR owner, for its tenant admins and platform admins.

The move commits in hr_owner_move (0522) on the app pool; the identity pool never touches domain
tables. The emails go after the commit, and nothing in that step can fail a move that has happened.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
import structlog
from fastapi import HTTPException, Request
from psycopg_pool import AsyncConnectionPool

from csa.console.auth import _invite_facts, _mailer
from csa.console.contracts import (
    ConsoleActor,
    HrOwnerMoveIn,
    HrOwnerMoveOut,
    HrOwnerOut,
    HrOwnerPerson,
)
from csa.console.identity import IdentityService
from csa.console.mail_templates import owner_moved_mail
from csa.console.services import require_package
from csa.core import db
from csa.core.tenancy import tx

logger = structlog.get_logger(__name__)

_HR_PACKAGE = frozenset({"hr"})
_PLATFORM_ACTOR_NAME = "FourStacks admin"
_ADMIN_REQUIRED = (403, "tenant administrator required")
_REFUSALS: dict[str, tuple[int, str]] = {
    "hr_owner_move_denied": _ADMIN_REQUIRED,
    "hr_idempotency_conflict": (409, "idempotency_conflict"),
    "hr_owner_read_only": (409, "owner_read_only"),
    "hr_owner_leaving": (409, "owner_leaving"),
    "hr_owner_unavailable": (409, "owner_unavailable"),
    "hr_owner_unchanged": (409, "owner_unchanged"),
    "hr_owner_changed": (409, "owner_changed"),
}


@contextmanager
def _database_refusals() -> Iterator[None]:
    """Name the functions' refusals. Any other database error stays an error."""
    try:
        yield
    except psycopg.errors.DeadlockDetected:
        # 0522 accepts one rare deadlock with person.leave. Nothing was written, so a retry is safe.
        raise HTTPException(409, "try_again") from None
    except (psycopg.errors.InsufficientPrivilege, psycopg.errors.CheckViolation) as exc:
        refusal = _REFUSALS.get(exc.diag.message_primary or "")
        if refusal is None:
            raise
        raise HTTPException(*refusal) from None


async def _package_active(pool: AsyncConnectionPool, client_id: UUID) -> bool:
    try:
        await require_package(pool, client_id, _HR_PACKAGE)
    except HTTPException as exc:
        if exc.detail != "package_inactive":
            raise
        return False
    return True


async def _session_context(conn: psycopg.AsyncConnection[Any], actor: ConsoleActor) -> None:
    """Both functions compare the actor with app.hr_user_id, a platform admin included."""
    await db.q.execute("console/invite_facts_context.sql", (str(actor.user_id),), conn=conn)


async def _owner(
    conn: psycopg.AsyncConnection[Any], actor: ConsoleActor, client_id: UUID
) -> HrOwnerPerson | None:
    return await db.q.fetch_one(
        "console/hr_owner_read.sql", (client_id, actor.user_id), conn=conn, model=HrOwnerPerson
    )


async def read_hr_owner(
    request: Request, actor: ConsoleActor, service: IdentityService, client_id: UUID
) -> HrOwnerOut:
    """Who the HR owner is. Package off: 200 with available=false, so the panel just hides."""
    # Authorise first, so the package read never answers for another tenant.
    service.require_member_admin(actor, client_id)
    pool = request.app.state.pool
    if not await _package_active(pool, client_id):
        return HrOwnerOut(available=False, owner=None)
    with _database_refusals():
        async with tx(pool, str(client_id)) as conn:
            await _session_context(conn, actor)
            owner = await _owner(conn, actor, client_id)
    return HrOwnerOut(available=True, owner=owner)


async def move_hr_owner(
    request: Request,
    body: HrOwnerMoveIn,
    actor: ConsoleActor,
    service: IdentityService,
    request_key: UUID,
) -> HrOwnerMoveOut:
    """Move the title, then send the emails. A committed move always answers 200."""
    service.require_member_admin(actor, body.client_id)
    pool = request.app.state.pool
    if not await _package_active(pool, body.client_id):
        raise HTTPException(422, "hr_not_available")
    fingerprint = hashlib.sha256(
        f"{body.new_owner_id}:{body.expected_owner_id or ''}".encode()
    ).hexdigest()
    with _database_refusals():
        async with tx(pool, str(body.client_id)) as conn:
            await _session_context(conn, actor)
            moved = await db.q.fetch_one(
                "console/hr_owner_move.sql",
                (
                    body.client_id,
                    actor.user_id,
                    body.new_owner_id,
                    body.expected_owner_id,
                    request_key,
                    fingerprint,
                ),
                conn=conn,
            )
            # In this transaction, so the answer needs nothing that can fail after the commit.
            owner = await _owner(conn, actor, body.client_id)
            if owner is None:  # Never expected: hr_client_owners has a foreign key to the member.
                raise RuntimeError("hr_owner_move left no readable owner")
    if moved["duplicate"]:
        return HrOwnerMoveOut(owner=owner, mail="repeat")
    mail = await _send_owner_mail(
        request,
        actor,
        service,
        body.client_id,
        old_owner=moved["old_owner"],
        new_owner=moved["new_owner"],
    )
    return HrOwnerMoveOut(owner=owner, mail=mail)


async def _send_owner_mail(
    request: Request,
    actor: ConsoleActor,
    service: IdentityService,
    client_id: UUID,
    *,
    old_owner: UUID | None,
    new_owner: UUID,
) -> Literal["sent", "not_sent"]:
    """The move has committed, so this only ever reports.

    Logs carry a category and no exception text, which can hold an address.
    """
    mailer = _mailer(request)
    if mailer is None:
        return "not_sent"
    moved_at = datetime.now(UTC)
    try:
        members = await service.members(actor, client_id)
        # mail_on=False: a bad company timezone falls back rather than refusing.
        facts = await _invite_facts(request, actor, client_id, mail_on=False)
        names = {member["user_id"]: member["display_name"] for member in members}
        # The old owner even when disabled: they may be the person who most needs to know.
        recipients = {
            member["email"]: member["display_name"]
            for member in members
            if member["user_id"] in (old_owner, new_owner)
            or (member["role"] == "tenant_admin" and member["is_enabled"])
        }
        actor_name = _PLATFORM_ACTOR_NAME if actor.platform_admin else actor.display_name
        outcomes = await asyncio.gather(
            *(
                mailer.send(
                    owner_moved_mail(
                        to=address,
                        name=name,
                        business_name=facts.business_name,
                        actor_name=actor_name,
                        old_owner_name=names.get(old_owner),
                        new_owner_name=names[new_owner],
                        moved_at=moved_at,
                        tz=facts.timezone,
                    )
                )
                for address, name in recipients.items()
            )
        )
    except Exception:
        logger.warning("hr_owner_mail_failed", category="unexpected_error")
        return "not_sent"
    return "sent" if all(outcome == "sent" for outcome in outcomes) else "not_sent"
