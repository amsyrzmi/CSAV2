"""A company's inbox settings (table public.inbox_settings, migration 0621).

A company has no row until its first save; :func:`load` then returns the defaults, which equal the
0621 column defaults (tests/test_inbox_queue.py compares them with information_schema).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field

from csa.core import db


class InboxSettings(BaseModel):
    """Response targets per priority (minutes) and the reminder knobs; ranges as the 0621 checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target_urgent_min: int = Field(default=15, ge=5, le=10080)
    target_high_min: int = Field(default=60, ge=5, le=10080)
    target_normal_min: int = Field(default=240, ge=5, le=10080)
    target_low_min: int = Field(default=1440, ge=5, le=10080)
    repeat_minutes: int = Field(default=60, ge=15, le=1440)
    max_repeats: int = Field(default=3, ge=0, le=10)
    # 0 turns the last three off.
    follow_up_hours: int = Field(default=48, ge=0, le=720)
    stale_claim_hours: int = Field(default=8, ge=0, le=168)
    handback_prompt_hours: int = Field(default=24, ge=0, le=720)

    def target_minutes(self, priority: str) -> int:
        """The response target for a priority: due_at = needs_staff_since + this many minutes."""
        targets = {
            "urgent": self.target_urgent_min,
            "high": self.target_high_min,
            "normal": self.target_normal_min,
            "low": self.target_low_min,
        }
        if priority not in targets:
            raise ValueError(f"unknown priority: {priority!r}")
        return targets[priority]


async def load(conn: AsyncConnection[Any], client_id: UUID) -> InboxSettings:
    """The company's settings, or the defaults when it has saved none."""
    row = await db.q.fetch_one("inbox_queue/get_settings.sql", (client_id,), conn=conn)
    return InboxSettings() if row is None else InboxSettings.model_validate(row)
