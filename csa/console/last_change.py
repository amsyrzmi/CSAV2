"""The "last changed" line: the newest audit row for a service package or agent capability.

csa_app cannot read console_audit or console_users, so rows come from the 0502 definer function
``console_last_change``, which filters by tenant itself. Only platform admins see who made the
change; everyone else gets a role label (a platform administrator, or a member of their own team),
never a name, user id or email.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from csa.core import db

Action = Literal[
    "requested",
    "activated",
    "suspended",
    "subscribed",
    "unsubscribed",
    "tenant_switch_on",
    "tenant_switch_off",
]
ROLE_LABEL = "a platform administrator"
TEAM_LABEL = "a member of your team"
SERVICE_ACTIONS: tuple[str, ...] = ("service.requested", "service.activated", "service.suspended")
CAPABILITY_ACTIONS: tuple[str, ...] = ("capability.updated",)
_SERVICE: dict[str, Action] = {
    "service.requested": "requested",
    "service.activated": "activated",
    "service.suspended": "suspended",
}
# Checked in order: when one audit row changed both switches, the subscription wins.
_SWITCHES: tuple[tuple[str, Action, Action], ...] = (
    ("is_enabled", "subscribed", "unsubscribed"),
    ("client_enabled", "tenant_switch_on", "tenant_switch_off"),
)


class LastChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Action
    at: datetime
    actor: str  # Display name for platform admins, otherwise a role label; never an id or email.


def _capability_action(details: Any) -> Action | None:
    if not isinstance(details, Mapping):
        return None
    before, after = details.get("before"), details.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    for key, on, off in _SWITCHES:
        was, now = before.get(key), after.get(key)
        if was is False and now is True:
            return on
        if was is True and now is False:
            return off
    return None


def last_change_from_row(
    row: Mapping[str, Any] | None, *, platform_admin: bool
) -> LastChange | None:
    """Map one console_last_change row; None when missing or not a recognisable change."""
    if row is None:
        return None
    action = row["action"]
    mapped = (
        _capability_action(row["details"])
        if action == "capability.updated"
        else _SERVICE.get(action)
    )
    if mapped is None:
        return None
    name = row["actor_name"]
    if platform_admin:
        actor = name or ROLE_LABEL
    else:
        actor = ROLE_LABEL if row["actor_is_platform_admin"] else TEAM_LABEL
    return LastChange(action=mapped, at=row["at"], actor=actor)


async def last_changes(
    conn: AsyncConnection[Any],
    client_id: UUID,
    targets: Sequence[str],
    actions: Sequence[str],
    *,
    platform_admin: bool,
) -> dict[str, LastChange]:
    """One query for many targets; the connection must be inside ``tx`` for ``client_id``."""
    if not targets:
        return {}
    rows = await db.q.fetch_all(
        "console/last_change.sql", (list(targets), client_id, list(actions)), conn=conn
    )
    result: dict[str, LastChange] = {}
    for row in rows:
        change = last_change_from_row(row, platform_admin=platform_admin)
        if change is not None:
            result[row["target"]] = change
    return result
