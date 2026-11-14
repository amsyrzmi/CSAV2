r"""Seed synthetic credit-monitoring data into the isolated local dev database (manual testing).

For the two sandbox companies of ``scripts/seed_dev.py``:

- ``sandbox-amsyar`` (the demo company) gets a pilot / Individual plan joining inside the current
  billing cycle (three days after its start, or today if that is earlier), and a few hundred
  synthetic ledger rows over the previous and the current cycle: all seven jobs, both providers,
  a three-call execution, a provider fallback, one execution straddling the current cycle's start
  and one execution that calls a model with no price (``UNPRICED_MODEL``), so the current cycle
  shows "Some usage is still being priced" until a platform admin adds that price.
- ``sandbox-ihsan`` gets a collaborator / Complete plan from the current cycle's start and no
  usage: the zero-usage state with the full platform fee.

Plan rows go through ``csa.billing.plans.insert_plan`` (the plan rules and the audit row), under
the earliest enabled platform admin, who must exist first. Ledger rows carry the same columns and
constraints as the app's own insert, with explicit times. Everything runs in one transaction.

Idempotent: a company that already has a plan row keeps it, and a trace already in the ledger is
not written again, so a second run adds nothing. Trace ids are keyed on the cycle's start date, so
a run in a later cycle adds that cycle's usage and leaves earlier cycles alone.

Refuses anything but the loopback dev owner database (``scripts.verify_reset_target``): run it
against a throwaway container, never the shared ``csa_dev_db`` other sessions use, and never a
staging or production database. Usage::

    ENV=dev SEED_DATABASE_URL=postgres://csa:<owner password>@127.0.0.1:<port>/csa \\
        uv run python -m scripts.seed_credit_demo
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, LiteralString, cast, get_args
from uuid import UUID

import psycopg
from csa.billing.cycles import KL, Cycle, cycle_containing, cycle_of, kl_today
from csa.billing.plans import Bundle, PlanRow, Stage, insert_plan
from csa.core import db
from csa.metering.context import Job
from pydantic import BaseModel, ConfigDict

from scripts.seed_dev import TENANTS, seed_id
from scripts.verify_reset_target import validate_dsn

ROOT = Path(__file__).resolve().parents[1]

DEMO_CLIENT, DEMO_SLUG, _ = TENANTS[0]
COLLABORATOR_CLIENT, COLLABORATOR_SLUG, _ = TENANTS[1]
JOBS: tuple[Job, ...] = get_args(Job)

GROQ_MODEL = "openai/gpt-oss-120b"  # priced by migration 0571
GEMINI_MODEL = "gemini-3.1-flash-lite"  # priced by migration 0571
UNPRICED_MODEL = "openai/gpt-oss-20b"  # no price row: the admin adds one in the manual test

PLAN_REASON = "Synthetic credit demo seed (scripts/seed_credit_demo.py)"
JOIN_OFFSET_DAYS = 3
PREVIOUS_TRACES = 110
CURRENT_TRACES = 140

# Per job: relative weight in the bulk traffic, the schema names its calls use in order, the most
# calls one execution makes, and the prompt / completion token ranges of one call.
_JOB_SHAPE: dict[Job, tuple[int, tuple[str, ...], int, tuple[int, int], tuple[int, int]]] = {
    "customer_turn": (40, ("Classification", "PlanOut", "ComposeOut"), 2, (900, 4200), (40, 420)),
    "workspace_assistant": (15, ("PlannerOutput", "DraftReview"), 2, (1800, 5200), (120, 700)),
    "hr_assistant": (7, ("HrChatPlan",), 1, (1200, 3600), (80, 500)),
    "email_analysis": (15, ("ThreadAnalysis",), 1, (1500, 4000), (150, 600)),
    "email_reply": (10, ("DraftProposal",), 1, (1400, 3800), (200, 700)),
    "email_compose": (8, ("DraftProposal",), 1, (900, 2600), (200, 650)),
    "training_summary": (5, ("SummaryDraft",), 1, (6000, 14000), (300, 900)),
}


@dataclass(frozen=True)
class DemoCall:
    """One synthetic ledger row."""

    trace_id: UUID
    occurred_at: datetime
    job: Job
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    schema_name: str
    conversation_id: UUID | None


class CreditSeedReport(BaseModel):
    """What one run did: rows and plan rows added, and the plans it left alone."""

    model_config = ConfigDict(extra="forbid")
    current_cycle: date
    demo_join_date: date
    usage_rows_added: int
    plans_added: int
    kept: list[str]


def _midnight(d: date) -> datetime:
    return datetime.combine(d, time(0), tzinfo=KL)


def demo_join_date(now: datetime) -> date:
    """The demo company's join date: three days into the current cycle, or today if earlier."""
    current = cycle_of(now)
    return min(current.start + timedelta(days=JOIN_OFFSET_DAYS), kl_today(now))


class _Calls:
    """Builds one company's synthetic calls for one cycle, deterministically."""

    def __init__(self, slug: str, cycle: Cycle) -> None:
        self.slug = slug
        self.cycle = cycle
        self.rng = random.Random(f"csa:credit-demo:{slug}:{cycle.start.isoformat()}")
        self.calls: list[DemoCall] = []

    def trace(self, label: str) -> UUID:
        return seed_id(self.slug, f"credit-demo:{self.cycle.start.isoformat()}:{label}")

    def call(
        self,
        trace_id: UUID,
        at: datetime,
        job: Job,
        schema_name: str,
        *,
        provider: str = "groq",
        model: str = GROQ_MODEL,
    ) -> None:
        _, _, _, prompt, completion = _JOB_SHAPE[job]
        conversation = seed_id(self.slug, f"credit-demo:conversation:{trace_id}")
        self.calls.append(
            DemoCall(
                trace_id=trace_id,
                occurred_at=at,
                job=job,
                provider=provider,
                model=model,
                prompt_tokens=self.rng.randint(*prompt),
                completion_tokens=self.rng.randint(*completion),
                schema_name=schema_name,
                conversation_id=conversation if job == "customer_turn" else None,
            )
        )

    def bulk(self, count: int, start: datetime, end: datetime) -> None:
        """``count`` ordinary executions spread evenly over ``[start, end)``, all priced."""
        jobs = list(_JOB_SHAPE)
        weights = [shape[0] for shape in _JOB_SHAPE.values()]
        span = end - start
        for i in range(count):
            at = start + span * ((i + self.rng.random()) / count)
            job = self.rng.choices(jobs, weights)[0]
            _, schemas, most, _, _ = _JOB_SHAPE[job]
            trace_id = self.trace(f"bulk:{i}")
            gemini = self.rng.random() < 0.08
            for n in range(self.rng.randint(1, most)):
                self.call(
                    trace_id,
                    at + timedelta(seconds=7 * n),
                    job,
                    schemas[min(n, len(schemas) - 1)],
                    provider="gemini" if gemini else "groq",
                    model=GEMINI_MODEL if gemini else GROQ_MODEL,
                )


def demo_calls(now: datetime) -> list[DemoCall]:
    """The demo company's calls for the previous and the current cycle, none after ``now``."""
    current = cycle_of(now)
    previous = cycle_containing(current.start - timedelta(days=1))
    start, join = _midnight(current.start), _midnight(demo_join_date(now))
    # Leave the last minutes before now empty, but never place a call before the cycle start.
    end = now - min(timedelta(minutes=10), (now - start) / 2)

    before = _Calls(DEMO_SLUG, previous)
    before.bulk(PREVIOUS_TRACES, _midnight(previous.start), start)

    cur = _Calls(DEMO_SLUG, current)
    cur.bulk(CURRENT_TRACES, start, end)
    # One execution that starts in the previous cycle and finishes in this one: it belongs to the
    # cycle of its first call.
    straddle = cur.trace("straddle")
    cur.call(straddle, start - timedelta(minutes=3), "customer_turn", "Classification")
    cur.call(straddle, min(start + timedelta(minutes=2), end), "customer_turn", "ComposeOut")
    # Every job once inside the billed window, so every agent shows in the per-agent table.
    window = end - join
    for i, job in enumerate(JOBS):
        cur.call(
            cur.trace(f"job:{job}"), join + window * (0.05 + 0.03 * i), job, _JOB_SHAPE[job][1][0]
        )
    # One customer reply that made three model calls: one execution.
    multi = cur.trace("multi")
    at = join + window * 0.4
    for n, schema in enumerate(("Classification", "PlanOut", "ComposeOut")):
        cur.call(multi, at + timedelta(seconds=11 * n), "customer_turn", schema)
    # One that fell back from Groq to Gemini: still one execution.
    fallback = cur.trace("fallback")
    at = join + window * 0.55
    cur.call(fallback, at, "customer_turn", "Classification")
    cur.call(
        fallback,
        at + timedelta(seconds=9),
        "customer_turn",
        "ComposeOut",
        provider="gemini",
        model=GEMINI_MODEL,
    )
    # One assistant request whose draft review used a model with no price: the whole execution is
    # unpriced until a platform admin back-dates that model's price.
    unpriced = cur.trace("unpriced")
    at = join + window * 0.7
    cur.call(unpriced, at, "workspace_assistant", "PlannerOutput")
    cur.call(
        unpriced,
        at + timedelta(seconds=14),
        "workspace_assistant",
        "DraftReview",
        model=UNPRICED_MODEL,
    )
    calls = before.calls + cur.calls
    late = [c for c in calls if c.occurred_at > now]
    if late:  # a construction error, never a silent drop
        raise RuntimeError(f"{len(late)} demo calls fall after now")
    return calls


async def _scope(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> None:
    await conn.execute(db.q.load("seed/credit_scope.sql"), (str(client_id),))


async def _add_plan(
    conn: psycopg.AsyncConnection[Any],
    actor: dict[str, Any],
    client_id: UUID,
    stage: Stage,
    bundle: Bundle,
    effective_from: date,
    now: datetime,
) -> bool:
    """Insert the company's first plan row through the plan rules, unless it has one already."""
    await _scope(conn, client_id)
    if await db.q.fetch_all("billing/plans_history.sql", (client_id,), conn=conn):
        return False
    await insert_plan(
        conn,
        actor["display_name"],
        client_id,
        PlanRow(stage, bundle, effective_from, now),
        PLAN_REASON,
        kl_today(now),
        actor_id=actor["id"],
    )
    return True


async def _add_usage(
    conn: psycopg.AsyncConnection[Any], client_id: UUID, calls: list[DemoCall]
) -> int:
    """Write the calls of every trace the ledger does not hold yet; returns rows written."""
    await _scope(conn, client_id)
    trace_ids = sorted({c.trace_id for c in calls})
    held = await db.q.fetch_all(
        "seed/credit_traces.sql", {"client_id": client_id, "trace_ids": trace_ids}, conn=conn
    )
    have = {row["trace_id"] for row in held}
    rows = [
        {
            "client_id": client_id,
            "occurred_at": c.occurred_at,
            "trace_id": c.trace_id,
            "conversation_id": c.conversation_id,
            "job": c.job,
            "provider": c.provider,
            "model": c.model,
            "prompt_tokens": c.prompt_tokens,
            "completion_tokens": c.completion_tokens,
            "schema_name": c.schema_name,
        }
        for c in sorted(calls, key=lambda c: c.occurred_at)
        if c.trace_id not in have
    ]
    if rows:
        async with conn.cursor() as cursor:
            await cursor.executemany(db.q.load("seed/credit_usage.sql"), rows)
    return len(rows)


async def seed_credit_demo(dsn: str, *, now: datetime | None = None) -> CreditSeedReport:
    """Seed the demo plans and usage as of ``now`` (aware; default the current time)."""
    if os.environ.get("ENV") not in {"dev", "ci"}:
        raise ValueError("The credit demo seed requires explicit ENV=dev or ENV=ci")
    validate_dsn(dsn)
    moment = datetime.now(UTC) if now is None else now
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("now must carry a timezone")
    current = cycle_of(moment)
    join = demo_join_date(moment)
    calls = demo_calls(moment)
    plans_wanted: tuple[tuple[UUID, str, Stage, Bundle, date], ...] = (
        (DEMO_CLIENT, DEMO_SLUG, "pilot", "individual", join),
        (COLLABORATOR_CLIENT, COLLABORATOR_SLUG, "collaborator", "complete", current.start),
    )
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        await db.q.execute("seed/lock.sql", conn=conn)
        applied = {row["version"] for row in await db.q.fetch_all("seed/schema.sql", conn=conn)}
        required = {path.name.split("_", 1)[0] for path in (ROOT / "db/migrations").glob("*.sql")}
        if required - applied:
            raise ValueError("The credit demo seed requires all checked-out SQL migrations")
        for client_id, slug, _ in TENANTS:
            rows = await db.q.fetch_all(
                "seed/tenant.sql", {"client_id": client_id, "slug": slug}, conn=conn
            )
            if any(r["id"] != client_id or r["slug"] != slug for r in rows):
                raise ValueError("Sandbox tenant ID/slug collision; no seed data committed")
        query = cast(LiteralString, (ROOT / "db/seed/sandbox_clients.sql").read_text())
        await conn.execute(query, prepare=False)
        actor = await db.q.fetch_one("seed/credit_actor.sql", conn=conn)
        if actor is None:
            raise ValueError(
                "No enabled platform admin: create one first (scripts.bootstrap_console_admin);"
                " plan rows are audited under a platform admin"
            )
        kept = []
        plans = 0
        for client_id, slug, stage, bundle, effective in plans_wanted:
            if await _add_plan(conn, actor, client_id, stage, bundle, effective, moment):
                plans += 1
            else:
                kept.append(f"{slug}: already has a plan row, left as it is")
        added = await _add_usage(conn, DEMO_CLIENT, calls)
    return CreditSeedReport(
        current_cycle=current.start,
        demo_join_date=join,
        usage_rows_added=added,
        plans_added=plans,
        kept=kept,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed synthetic credit-monitoring plans and usage into the isolated dev DB."
    )
    parser.parse_args()
    dsn = os.environ.get("SEED_DATABASE_URL")
    if not dsn:
        parser.error("SEED_DATABASE_URL must select the isolated loopback dev owner database")
    report = asyncio.run(seed_credit_demo(dsn))
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
