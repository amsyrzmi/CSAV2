"""The company's inbox settings and the people a conversation can be assigned to.

Settings follow the bookings pattern: no row until the first save (the API answers the defaults of
``csa.inbox_queue.settings`` with version 0), a whole-record PUT carrying the version it read, an
advisory lock, a 409 ``version_conflict`` for a stale version and an audit row. A save changes only
items that enter ``needs_staff`` afterwards: ``due_at`` is fixed when an item enters.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from pydantic import Field

from csa.console.contracts import ConsoleActor, Contract
from csa.console.kb_uploads import audit
from csa.core import db
from csa.core.tenancy import tx
from csa.inbox_queue.settings import InboxSettings

# The ranges are those of csa.inbox_queue.settings.InboxSettings and the 0621 checks
# (tests/test_inbox_queue_api.py compares them with the model).
Target = Annotated[int, Field(ge=5, le=10080)]


class InboxSettingsIn(Contract):
    """Every setting, replaced as a whole. `version` is the one read: 0 while nothing has been
    saved, then the number the last save returned. 0 turns the last three settings off."""

    target_urgent_min: Target
    target_high_min: Target
    target_normal_min: Target
    target_low_min: Target
    repeat_minutes: Annotated[int, Field(ge=15, le=1440)]
    max_repeats: Annotated[int, Field(ge=0, le=10)]
    follow_up_hours: Annotated[int, Field(ge=0, le=720)]
    stale_claim_hours: Annotated[int, Field(ge=0, le=168)]
    handback_prompt_hours: Annotated[int, Field(ge=0, le=720)]
    version: int = Field(ge=0)


class InboxSettingsOut(InboxSettingsIn):
    """The settings as stored, or the defaults with `version` 0 while none are saved."""


class InboxAssignee(Contract):
    """A person a conversation can be assigned to. No address is ever sent."""

    id: UUID
    display_name: str
    role: Literal["tenant_admin", "tenant_staff"]


def _version_conflict() -> HTTPException:
    return HTTPException(409, "version_conflict")


async def _stored(conn: AsyncConnection[Any], client_id: UUID) -> dict[str, Any] | None:
    return await db.q.fetch_one("inbox/settings_get.sql", {"client_id": client_id}, conn=conn)


async def get_settings(conn: AsyncConnection[Any], client_id: UUID) -> InboxSettingsOut:
    """The stored settings, or the defaults with version 0."""
    row = await _stored(conn, client_id)
    if row is None:
        return InboxSettingsOut(**InboxSettings().model_dump(), version=0)
    return InboxSettingsOut.model_validate(row)


async def put_settings(
    conn: AsyncConnection[Any], client_id: UUID, actor: ConsoleActor, data: InboxSettingsIn
) -> InboxSettingsOut:
    """Replace the settings. The first save makes the row; a save from an older read is 409."""
    await db.q.fetch_one("inbox/lock_request.sql", (f"inbox:settings:{client_id}",), conn=conn)
    current = await _stored(conn, client_id)
    if (current["version"] if current else 0) != data.version:
        raise _version_conflict()
    values = data.model_dump(exclude={"version"}) | {"client_id": client_id}
    await db.q.execute(
        "inbox/settings_update.sql" if current else "inbox/settings_insert.sql", values, conn=conn
    )
    await audit(conn, actor, client_id, "inbox.settings.updated", str(client_id))
    return await get_settings(conn, client_id)


async def company_assignees(
    identity_pool: AsyncConnectionPool | None, client_id: UUID
) -> list[InboxAssignee]:
    """Enabled administrators and staff of the company, with an enabled account, by display name.

    ``csa_app`` cannot read ``console_users`` or ``console_memberships``, so this reads on the
    identity pool inside a transaction for the company. Without that pool the directory is
    unavailable: 503, never an empty list that would read as "nobody to assign to".
    """
    if identity_pool is None:
        raise HTTPException(503, "member directory unavailable")
    async with tx(identity_pool, str(client_id)) as conn:
        rows = await db.q.fetch_all("inbox/assignees.sql", {"client_id": client_id}, conn=conn)
    return [InboxAssignee.model_validate(row) for row in rows]
