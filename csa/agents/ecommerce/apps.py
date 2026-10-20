"""Global metadata fence; app secrets stay in an explicitly versioned external registry."""

from typing import Any, Literal

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field

from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.core import db


class TikTokAppState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    app_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(strict=True, ge=1)
    state: Literal["active", "disabled"]


async def register_app(conn: AsyncConnection[Any], app_id: str) -> TikTokAppState:
    """Owner/operator connection only; register disabled, with no secrets or activation."""
    initial = TikTokAppState(app_id=app_id, revision=1, state="disabled")
    row = await db.q.fetch_one("ecommerce/register_app.sql", (initial.app_id,), conn=conn)
    if row is None:
        raise TikTokAuthorizationError("app_registration_conflict")
    return TikTokAppState.model_validate(row)


async def transition_app(
    conn: AsyncConnection[Any],
    *,
    app_id: str,
    expected_revision: int,
    state: Literal["active", "disabled"],
) -> TikTokAppState:
    """CAS on an owner/operator connection; caller commits before external mutation.

    Disable commits before external secret replacement. Stage an immutable external
    snapshot at the next revision before activation. Never overwrite an active snapshot.
    Old grants remain revision-mismatched even after activation; reauthorization is separate.
    """
    target = TikTokAppState(app_id=app_id, revision=expected_revision, state=state)
    row = await db.q.fetch_one(
        "ecommerce/transition_app.sql",
        {"app_id": target.app_id, "revision": target.revision, "state": target.state},
        conn=conn,
    )
    if row is None:
        raise TikTokAuthorizationError("stale_app_state")
    return TikTokAppState.model_validate(row)


async def require_app(conn: AsyncConnection[Any], app_id: str, revision: int) -> None:
    """Hold a shared app fence until caller commit; lock before credential then grant."""
    row = await db.q.fetch_one("ecommerce/lock_app.sql", (app_id, revision), conn=conn)
    if row is None or not row["allowed"]:
        raise TikTokAuthorizationError("app_revision_unavailable")
