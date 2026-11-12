"""A company's billing plan history (table ``client_billing_plans``, migration 0572) and its rules.

Spec "0552_client_billing_plans.sql" (migration 0572 here), rules 1 to 7. A row says "from this
Kuala Lumpur date, at 00:00, the company is on this stage and bundle". Rows are never edited or
removed: a mistake is corrected by a newer row with the same date, and the newest wins.

Everything here is pure except ``insert_plan``, the one database path (platform admin, Task 10).
``history`` arguments are a company's rows in any order; exact ties on ``(effective_from,
created_at)`` go to the row later in the sequence (``plans_history.sql`` orders by id last).

Words used below: the *join date* is the earliest ``effective_from`` (the first row's date,
which no later row may precede); the *current cycle* is the cycle containing ``today``, the Kuala
Lumpur date.
"""

from __future__ import annotations

import calendar
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from csa.billing.cycles import Cycle, cycle_containing
from csa.core import db

__all__ = [
    "Bundle",
    "PlanRow",
    "PlanRuleCode",
    "PlanRuleError",
    "Stage",
    "billed_plan",
    "billed_window",
    "insert_plan",
    "pilot_ended",
    "plan_for_cycle",
    "validate_new_plan",
]

Stage = Literal["pilot", "collaborator", "terminated"]
Bundle = Literal["individual", "curated", "complete"]
PlanRuleCode = Literal[
    "after_termination",
    "before_current_cycle",
    "before_join_date",
    "first_row_must_be_pilot_or_collaborator",
    "first_row_outside_current_cycle",
    "change_not_on_a_fifth",
    "bundle_change_same_stage_only",
]

_PILOT_MONTHS = 3
_AUDIT_ACTION = "billing.plan_inserted"


@dataclass(frozen=True)
class PlanRow:
    """One row of plan history: ``stage`` and ``bundle`` from ``effective_from`` (KL date)."""

    stage: Stage
    bundle: Bundle
    effective_from: date
    created_at: datetime


class PlanRuleError(ValueError):
    """A new plan row breaks one of the plan-history rules; ``code`` names which."""

    def __init__(self, code: PlanRuleCode, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code: PlanRuleCode = code


def _latest(rows: Sequence[PlanRow]) -> PlanRow | None:
    """Greatest ``effective_from``, then newest ``created_at``, then later in the sequence."""
    if not rows:
        return None
    _, _, _, winner = max(
        ((r.effective_from, r.created_at, i, r) for i, r in enumerate(rows)),
        key=lambda t: t[:3],
    )
    return winner


def _join_date(history: Sequence[PlanRow]) -> date | None:
    return min((r.effective_from for r in history), default=None)


def _termination_date(history: Sequence[PlanRow]) -> date | None:
    return min((r.effective_from for r in history if r.stage == "terminated"), default=None)


def _until_termination(history: Sequence[PlanRow]) -> list[PlanRow]:
    """Termination is final: drop every row dated after the earliest termination date.

    Rows on or before that date still count, and same-date rows follow newest-wins, so a row
    scheduled for a date after the company left never applies.
    """
    terminated = _termination_date(history)
    return [r for r in history if terminated is None or r.effective_from <= terminated]


def plan_for_cycle(history: Sequence[PlanRow], cycle: Cycle) -> PlanRow | None:
    """The row that applies to ``cycle``: the latest whose ``effective_from`` is before its end.

    Terminated rows are included, so a cycle containing or after a termination yields the
    ``terminated`` row. Termination is final: rows dated after the earliest termination are ignored.
    ``None`` only for cycles before the first row. Use ``billed_window`` for the billed period and
    ``billed_plan`` for the stage and bundle that price it.
    """
    return _latest([r for r in _until_termination(history) if r.effective_from < cycle.end])


def billed_window(history: Sequence[PlanRow], cycle: Cycle) -> tuple[date, date] | None:
    """The billed period of ``cycle`` as ``(start, end)``, end exclusive, or ``None``.

    Start is the cycle start or the join date, if later. End is the termination date when it falls
    inside the cycle, otherwise the cycle end. ``None`` when billing is not set up for the cycle (no
    row dated before its end) or nothing is billed because the company was terminated on or before
    the window would start (a termination dated the cycle start, or the join date). Rows dated after
    the earliest termination are ignored.
    """
    history = _until_termination(history)
    join = _join_date(history)
    if join is None or join >= cycle.end:
        return None
    start = max(cycle.start, join)
    end = cycle.end
    terminated = _termination_date(history)
    if terminated is not None:
        if terminated <= start:
            return None
        end = min(end, terminated)
    return (start, end)


def billed_plan(history: Sequence[PlanRow], cycle: Cycle) -> PlanRow | None:
    """The row whose stage and bundle price ``cycle``'s billed window, or ``None`` when unbilled.

    Spec rule 3: the latest row that is not ``terminated`` and is dated before the cycle's end. In
    the cycle a termination falls in, this is the plan before it (the final period is billed on that
    plan, prorated); in later cycles ``billed_window`` is ``None`` and so is this.
    """
    if billed_window(history, cycle) is None:
        return None
    return _latest(
        [
            r
            for r in _until_termination(history)
            if r.stage != "terminated" and r.effective_from < cycle.end
        ]
    )


def validate_new_plan(history: Sequence[PlanRow], new: PlanRow, today: date) -> None:
    """Refuse ``new`` with ``PlanRuleError`` if it breaks a rule; ``today`` is the KL date.

    ``new.created_at`` plays no part. Checks run in this order and the first failure is raised, so
    the most specific code wins:

    First row (empty history), spec rule 2:

    1. ``first_row_must_be_pilot_or_collaborator``: stage is ``terminated``.

    2. ``first_row_outside_current_cycle``: the join date is not inside the current cycle (earlier
    would create a closed cycle, later is not "any date within the current cycle").

    Any later row:

    3. ``after_termination`` (rule 4): the history has a ``terminated`` row, whatever the new row's
    date.

    4. ``before_current_cycle`` (rule 5): dated before the current cycle's start.

    5. ``before_join_date`` (rule 2): dated before the first row's date. No plan existed then to
    change or end, and the join date is where billing starts.

    A termination that passes 3 to 5 is accepted (rule 4: any day), even when a row is already
    scheduled for a later date: termination is final, and the readers ignore every row dated after
    the earliest termination.

    6. ``change_not_on_a_fifth`` (rule 3): a pilot or collaborator row not dated a 5th. The one
    exception is a same-date correction of the first row, which is dated the join date.

    7. ``bundle_change_same_stage_only`` (rule 2): a pilot row may only follow a pilot row (a bundle
    change, same stage); a row may not leave a later existing pilot row following a collaborator.
    Collaborator and terminated rows may follow either stage.

    A row dated the same day as existing rows is a correction: it is checked against the plan in
    force before that day (the rows it replaces are ignored), and it wins because it is newest.
    """
    current = cycle_containing(today)
    if not history:
        if new.stage == "terminated":
            raise PlanRuleError(
                "first_row_must_be_pilot_or_collaborator",
                "the first row starts billing, so it is a pilot or collaborator row",
            )
        if not current.start <= new.effective_from < current.end:
            raise PlanRuleError(
                "first_row_outside_current_cycle",
                f"the join date {new.effective_from.isoformat()} must fall within the current"
                f" cycle, {current.start.isoformat()} to {current.end.isoformat()}",
            )
        return

    day = new.effective_from
    if _termination_date(history) is not None:
        raise PlanRuleError("after_termination", "no row is accepted after a terminated row")
    if day < current.start:
        raise PlanRuleError(
            "before_current_cycle",
            f"{day.isoformat()} is before the current cycle's start, {current.start.isoformat()}",
        )
    join = min(r.effective_from for r in history)
    if day < join:
        raise PlanRuleError(
            "before_join_date", f"{day.isoformat()} is before the join date, {join.isoformat()}"
        )
    if new.stage == "terminated":
        # Rule 4: any day from the current cycle's start. Rows already scheduled after it stay
        # in the history but are ignored by every reader: termination is final.
        return

    if day != join and day.day != 5:
        raise PlanRuleError(
            "change_not_on_a_fifth",
            f"a stage or bundle change is dated a cycle start (a 5th), not {day.isoformat()}",
        )
    before = _latest([r for r in history if r.effective_from < day])
    if new.stage == "pilot" and before is not None and before.stage != "pilot":
        raise PlanRuleError(
            "bundle_change_same_stage_only",
            f"a pilot row may only follow a pilot row; the plan before {day.isoformat()}"
            f" is {before.stage}",
        )
    next_day = min((r.effective_from for r in history if r.effective_from > day), default=None)
    after = _latest([r for r in history if r.effective_from == next_day])
    if new.stage == "collaborator" and after is not None and after.stage == "pilot":
        raise PlanRuleError(
            "bundle_change_same_stage_only",
            f"the pilot row dated {after.effective_from.isoformat()} would follow this"
            " collaborator row",
        )


def _add_months(d: date, months: int) -> date:
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _first_cycle_start_on_or_after(d: date) -> date:
    cycle = cycle_containing(d)
    return cycle.start if cycle.start == d else cycle.end


def pilot_ended(history: Sequence[PlanRow], today: date) -> bool:
    """True when the pilot has run its course and the company is still on pilot terms.

    Spec rule 6: the pilot lasts three months from the first row's date. From the first cycle start
    on or after that point, if the plan for the cycle containing ``today`` (the KL date) is still
    ``pilot``, the admin is told "pilot ended: convert or terminate". A termination in that cycle
    ends the warning; a conversion scheduled for a later cycle does not, since this cycle is still
    billed as pilot. Nothing changes automatically.
    """
    join = _join_date(history)
    if join is None:
        return False
    if today < _first_cycle_start_on_or_after(_add_months(join, _PILOT_MONTHS)):
        return False
    plan = plan_for_cycle(history, cycle_containing(today))
    return plan is not None and plan.stage == "pilot"


def _audit_plan(plan: PlanRow | None) -> dict[str, str] | None:
    if plan is None:
        return None
    return {
        "stage": plan.stage,
        "bundle": plan.bundle,
        "effective_from": plan.effective_from.isoformat(),
    }


async def insert_plan(
    conn: psycopg.AsyncConnection[Any],
    actor_name: str,
    client_id: UUID,
    row: PlanRow,
    reason: str,
    today: date,
    *,
    actor_id: UUID,
) -> PlanRow:
    """Insert ``row`` for ``client_id`` if the rules allow it, and audit it; returns the row.

    Runs inside the caller's ``tx(pool, str(client_id))`` (the platform-admin route sets the tenant
    from the path's client id, as ``csa/console/hr_owner.py`` does); a connection whose
    ``app.client_id`` is another company, or that is not in a transaction, is refused with
    ``RuntimeError``. A per-company advisory lock serialises inserts so two admins cannot both pass
    the rules against the same history. ``row.created_at`` is not stored: the database sets
    ``created_at``, and the returned row carries it. ``created_by`` is ``actor_name``.

    The audit row goes to ``console_audit`` through ``console/audit.sql``, the table the
    platform-admin write in ``hr_owner_move`` (0522) audits to: action ``billing.plan_inserted``,
    target the new row's id, ``actor_id`` the admin's console user, and details ``before`` (the plan
    in force from the new row's date before this insert, or null), ``after``, ``reason`` and
    ``created_by``. A ``PlanRuleError`` writes nothing.
    """
    # Before any query: a statement would itself open an implicit transaction.
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError("insert_plan must run inside a transaction: use tx(pool, client_id)")
    tenant = await db.q.fetch_one("billing/plans_tenant.sql", conn=conn)
    if tenant is None or tenant["client_id"] != client_id:
        raise RuntimeError(
            "insert_plan: app.client_id is not this company; open tx(pool, client_id) first"
        )
    await db.q.execute("billing/plans_lock.sql", (f"client_billing_plans:{client_id}",), conn=conn)
    history = [
        PlanRow(**r)
        for r in await db.q.fetch_all("billing/plans_history.sql", (client_id,), conn=conn)
    ]
    validate_new_plan(history, row, today)
    before = _latest([r for r in history if r.effective_from <= row.effective_from])
    inserted = await db.q.fetch_one(
        "billing/plans_insert.sql",
        {
            "client_id": client_id,
            "stage": row.stage,
            "bundle": row.bundle,
            "effective_from": row.effective_from,
            "reason": reason,
            "created_by": actor_name,
        },
        conn=conn,
    )
    if inserted is None:  # INSERT ... RETURNING always returns the row or raises.
        raise RuntimeError("plans_insert.sql returned no row")
    stored = PlanRow(
        inserted["stage"], inserted["bundle"], inserted["effective_from"], inserted["created_at"]
    )
    await db.q.execute(
        "console/audit.sql",
        (
            actor_id,
            client_id,
            _AUDIT_ACTION,
            str(inserted["id"]),
            Jsonb(
                {
                    "before": _audit_plan(before),
                    "after": _audit_plan(stored),
                    "reason": reason,
                    "created_by": actor_name,
                }
            ),
        ),
        conn=conn,
    )
    return stored
