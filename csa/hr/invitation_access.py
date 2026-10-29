"""HR access chosen on an invitation: applied when the invitee accepts, or left for the HR owner.

Acceptance commits first. Then `apply_on_accept` makes one narrow call with no HR session, and the
definer function checks the token, the member and the chooser again before it adds the preset's
rows. A preset other than Staff whose chooser is no longer the HR owner is left pending, and the
HR owner applies or dismisses it on "Who can do what".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
import structlog
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from csa.core.db import q as sql
from csa.core.tenancy import tx
from csa.hr.access_contracts import (
    PAGE_SIZE,
    AccessPersonResult,
    PendingInvitationAccess,
    PendingInvitationPage,
)
from csa.hr.access_switches import EMPTY, PRESETS, AccessRows, PresetId, rows, state
from csa.hr.operations import HrOperations
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService

Outcome = Literal["applied", "none", "pending", "failed"]
logger = structlog.get_logger(__name__)

# Database refusals (the RAISE message) and the HR policy code each one becomes.
_REFUSALS = {
    "hr_invitation_unavailable": "resource_unavailable",
    "hr_invitation_applied": "invitation_already_handled",
    "hr_invitation_owner_required": "invitation_owner_required",
    "hr_invitation_rows_refused": "invitation_rows_refused",
    "hr_invitation_arguments_invalid": "invitation_arguments_invalid",
    "hr_owner_required": "permission_denied",
}


def preset_rows(preset: PresetId) -> dict[str, Any]:
    """The preset's rows in the hr_owner_access_summary JSON shapes."""
    found = rows(PRESETS[preset], EMPTY)
    return {
        "grants": [
            {"action": g.action, "scope": g.scope, "fields": list(g.fields)}
            for g in sorted(found.grants)
        ],
        "workflows": [
            {"domain": w.domain, "action": w.action, "scope": w.scope}
            for w in sorted(found.workflows)
        ],
        "areas": sorted(found.areas),
    }


def switches_after(current: Mapping[str, Any], preset: PresetId) -> tuple[list[str], list[str]]:
    """The member's switches once the preset is added to their current rows, and the ones it adds.

    These are recorded for Recent changes only; the database never authorizes on them.
    """
    before = AccessRows.from_summary(current["grants"], current["workflows"], current["areas"])
    after = before.union(rows(PRESETS[preset], EMPTY))
    on_before, on_after = state(before).on, state(after).on
    return sorted(on_after), sorted(on_after - on_before)


async def _refusing(conn: AsyncConnection, path: str, params: tuple[Any, ...]) -> None:
    try:
        await sql.execute(path, params, conn=conn)
    except (psycopg.errors.CheckViolation, psycopg.errors.InsufficientPrivilege) as exc:
        code = _REFUSALS.get(exc.diag.message_primary or "")
        if code is None:
            raise
        raise HrPolicyError(code) from None


class InvitationAccess:
    def __init__(self, core: HrService):
        self.core = core

    async def _lock(self, conn: AsyncConnection, tenant: UUID) -> None:
        # The same per-tenant lock as every other HR operation, so access saves never interleave.
        await sql.execute("hr/key_lock.sql", (f"{tenant}:hr-operations",), conn=conn)

    async def apply_on_accept(self, client_id: UUID, token_id: UUID) -> Outcome:
        """Never raises: acceptance has committed, and a failure only leaves the access pending.

        "none" means there is nothing to apply: no preset, or it was applied or dismissed already.
        """
        try:
            return await self._apply_on_accept(client_id, token_id)
        except HrPolicyError as exc:
            category = exc.code
        except psycopg.Error:
            category = "database_error"
        except Exception:
            category = "unexpected_error"
        logger.warning("hr_invitation_access_failed", category=category)
        return "failed"

    async def _apply_on_accept(self, client_id: UUID, token_id: UUID) -> Outcome:
        async with tx(self.core.pool, str(client_id)) as conn:
            found = await sql.fetch_one(
                "hr/invitation_preset.sql", (client_id, token_id), conn=conn
            )
            if found is None or found["applied"]:
                return "none"
            preset = cast(PresetId, found["preset"])
            if preset != "staff" and not found["chooser_is_owner"]:
                return "pending"
            # The accept request must not hang on a busy tenant: a lock wait past 5 s raises
            # LockNotAvailable, logged as database_error, and the access stays pending.
            await sql.execute("hr/invitation_lock_timeout.sql", conn=conn)
            await self._write(conn, client_id, token_id, override=False)
        return "applied"

    async def _write(
        self, conn: AsyncConnection, tenant: UUID, token_id: UUID, *, override: bool
    ) -> UUID:
        """Take the HR operations lock, read the member's rows afresh, and apply the preset."""
        await self._lock(conn, tenant)
        found = await sql.fetch_one("hr/invitation_preset.sql", (tenant, token_id), conn=conn)
        if found is None:
            raise HrPolicyError("resource_unavailable")
        if found["applied"]:
            raise HrPolicyError("invitation_already_handled")
        preset = cast(PresetId, found["preset"])
        on, added = switches_after(found, preset)
        await _refusing(
            conn,
            "hr/invitation_apply.sql",
            (tenant, token_id, Jsonb(preset_rows(preset)), on, added, override),
        )
        return found["user_id"]

    async def _owner(self, conn: AsyncConnection, session: HrSession, *, mutation: bool) -> None:
        if mutation and (session.csrf_hash is None or session.capability is not None):
            raise HrPolicyError("human_operation_required")
        HrOperations.owner(await self.core._access(conn, session))

    async def pending(self, session: HrSession) -> PendingInvitationPage:
        async with tx(self.core.pool, str(session.tenant_id)) as conn:
            await self._owner(conn, session, mutation=False)
            found = await sql.fetch_all(
                "hr/invitation_pending.sql", (session.tenant_id, PAGE_SIZE), conn=conn
            )
            await self.core._session(conn, session)
        return PendingInvitationPage(
            items=[PendingInvitationAccess.model_validate(row) for row in found]
        )

    async def apply(self, session: HrSession, token_id: UUID) -> AccessPersonResult:
        """The HR owner's own authority stands in, so a chooser who lost owner status is fine."""
        tenant = session.tenant_id
        async with tx(self.core.pool, str(tenant)) as conn:
            await self._owner(conn, session, mutation=True)
            user = await self._write(conn, tenant, token_id, override=True)
            await self.core._session(conn, session)
        return AccessPersonResult(user_id=user, outcome="saved")

    async def dismiss(self, session: HrSession, token_id: UUID) -> None:
        tenant = session.tenant_id
        async with tx(self.core.pool, str(tenant)) as conn:
            await self._owner(conn, session, mutation=True)
            await self._lock(conn, tenant)
            await _refusing(conn, "hr/invitation_dismiss.sql", (tenant, token_id))
            await self.core._session(conn, session)
