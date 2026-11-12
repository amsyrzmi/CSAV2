"""The cycle usage summary (``csa.billing.service``): ledger rows to executions, prices and the
bill, against the real migrated schema. Disposable database, synthetic companies and rows only.

The price, FX, fee and tier tables are global, and one database serves this whole module, so a
test that adds a config row uses a synthetic model of its own, or dates in 2031 that no other
test touches.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
import pytest
from csa.billing.agents import AGENT_TITLES, agent_for
from csa.billing.cycles import KL, Cycle, cycle_containing
from csa.billing.service import CycleUsage, cycle_usage, list_cycles
from csa.metering.context import JOB_AGENT

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner
from tests.test_training_schema import _company

pytestmark = requires_docker

AUG = Cycle(date(2026, 8, 5), date(2026, 9, 5))
SEP = Cycle(date(2026, 9, 5), date(2026, 10, 5))
OCT = Cycle(date(2026, 10, 5), date(2026, 11, 5))
JUL = Cycle(date(2026, 7, 5), date(2026, 8, 5))
MAY_31 = Cycle(date(2031, 5, 5), date(2031, 6, 5))
JUN_31 = Cycle(date(2031, 6, 5), date(2031, 7, 5))

SYN = "synthetic"
# 1 USD per million input tokens: 1,000 prompt tokens cost RM0.004084 at the seeded FX 4.0840.
FLAT = "flat"
UNPRICED = "no-price"  # never given a price row
VECTOR_1K = "vector-1k"  # 1,000,000 prompt tokens cost RM0.0167297 (workbook RM0.01673)
VECTOR_20K = "vector-20k"  # 1,000,000 prompt tokens cost RM0.01673664 (workbook RM0.0167365)
STABLE = "stable"  # the closed-cycle stability test's model (repriced in 2031)
OLD = datetime(2020, 1, 1, tzinfo=UTC)
CENT = Decimal("0.01")


@pytest.fixture(scope="module")
def prices(migrated_dsn: str) -> None:
    """Synthetic model prices for this module, effective 2020 like the seeds."""
    with _owner(migrated_dsn) as conn:
        for model, input_usd in (
            (FLAT, "1"),
            (VECTOR_1K, "0.0040964"),
            (VECTOR_20K, "0.0040981"),
            (STABLE, "1"),
        ):
            conn.execute(
                "INSERT INTO ai_model_prices"
                " (provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from)"
                " VALUES (%s, %s, %s, 0, %s)",
                (SYN, model, Decimal(input_usd), OLD),
            )


def _plan(dsn: str, client: str, stage: str, bundle: str, effective: date) -> None:
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO client_billing_plans (client_id, stage, bundle, effective_from, reason,"
            " created_by) VALUES (%s, %s, %s, %s, 'synthetic', 'Synthetic Admin')",
            (client, stage, bundle, effective),
        )


@dataclass(frozen=True)
class Row:
    at: datetime
    trace: uuid.UUID
    job: str = "customer_turn"
    provider: str = SYN
    model: str = FLAT
    prompt: int = 1000
    completion: int = 0


def _row(at: datetime, trace: uuid.UUID | None = None, **extra: Any) -> Row:
    return Row(at, trace or uuid.uuid4(), **extra)


def _ledger(dsn: str, client: str, rows: Sequence[Row]) -> None:
    with _owner(dsn) as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO ai_usage_events (client_id, occurred_at, trace_id, job, provider,"
                " model, prompt_tokens, completion_tokens, schema_name)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Synthetic')",
                (client, r.at, r.trace, r.job, r.provider, r.model, r.prompt, r.completion),
            )


def _bulk(
    dsn: str,
    client: str,
    start: datetime,
    count: int,
    *,
    model: str = FLAT,
    prompt: int = 1000,
    job: str = "customer_turn",
) -> None:
    """``count`` one-call executions, one second apart from ``start``, in one statement."""
    with _owner(dsn) as conn:
        conn.execute(
            "INSERT INTO ai_usage_events (client_id, occurred_at, trace_id, job, provider, model,"
            " prompt_tokens, completion_tokens, schema_name)"
            " SELECT %s, %s::timestamptz + g * interval '1 second', gen_random_uuid(), %s, %s,"
            " %s, %s, 0, 'Synthetic' FROM generate_series(0, %s - 1) AS g",
            (client, start, job, SYN, model, prompt, count),
        )


def _kl(y: int, m: int, d: int, h: int = 12, minute: int = 0) -> datetime:
    return datetime(y, m, d, h, minute, tzinfo=KL)


async def _usage(app_dsn: str, client: str, cycle: Cycle, now: datetime) -> CycleUsage:
    """``cycle_usage`` as the console will run it: the app role, scoped to ``client``."""
    return await _usage_as(app_dsn, client, client, cycle, now)


async def _usage_as(
    app_dsn: str, scope: str, client: str, cycle: Cycle, now: datetime
) -> CycleUsage:
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            # The same transaction-local setting tx(pool, client_id) makes.
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (scope,))
            return await cycle_usage(conn, uuid.UUID(client), cycle, now=now)


async def _cycles(app_dsn: str, client: str, now: datetime) -> list[Any]:
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (client,))
            return await list_cycles(conn, uuid.UUID(client), now)


def _assert_sums_exact(usage: CycleUsage) -> None:
    assert usage.bill is not None
    charge = usage.bill.ai_charge
    assert sum((p.price for p in usage.priced), Decimal(0)) == charge
    assert sum((a.price for a in usage.by_agent), Decimal(0)) == charge
    assert sum((d.price for d in usage.daily), Decimal(0)) == charge
    assert sum(a.executions for a in usage.by_agent) == len(usage.priced)


def _near(value: Decimal, expected: str) -> bool:
    return abs(value - Decimal(expected)) <= CENT


# --- agents ----------------------------------------------------------------------------------


def test_agent_titles_mirror_the_console_and_cover_every_job() -> None:
    assert AGENT_TITLES == {
        "support": "Customer support assistant",
        "assistant": "Workspace assistant",
        "hr": "HR assistant",
        "email": "Email assistant",
        "training": "Training summary",
    }
    assert set(JOB_AGENT.values()) == set(AGENT_TITLES)
    assert agent_for("email_reply") == ("email", "Email assistant")
    assert agent_for("customer_turn") == ("support", "Customer support assistant")
    assert agent_for("training_summary") == ("training", "Training summary")


# --- which executions a cycle holds ----------------------------------------------------------


async def test_cycle_boundary_is_kl_midnight_on_the_fifth(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    before, after = uuid.uuid4(), uuid.uuid4()
    _ledger(
        migrated_dsn,
        client,
        [
            _row(datetime(2026, 9, 4, 15, 59, 59, tzinfo=UTC), before),  # 4 Sep 23:59:59 KL
            _row(datetime(2026, 9, 4, 16, 0, 0, tzinfo=UTC), after),  # 5 Sep 00:00:00 KL
        ],
    )
    now = _kl(2026, 9, 10)
    aug, sep = await _usage(app_dsn, client, AUG, now), await _usage(app_dsn, client, SEP, now)
    assert [e.trace_id for e in aug.executions] == [before]
    assert [e.trace_id for e in sep.executions] == [after]
    assert aug.daily[-1].day == date(2026, 9, 4) and aug.daily[-1].price > 0
    assert sep.daily[0].day == date(2026, 9, 5) and sep.daily[0].price > 0


async def test_an_execution_straddling_the_boundary_belongs_to_its_first_row(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    trace = uuid.uuid4()
    _ledger(
        migrated_dsn,
        client,
        [
            _row(datetime(2026, 9, 4, 15, 59, 50, tzinfo=UTC), trace, prompt=100, completion=10),
            _row(datetime(2026, 9, 4, 16, 0, 10, tzinfo=UTC), trace, prompt=200, completion=20),
        ],
    )
    now = _kl(2026, 9, 10)
    aug = await _usage(app_dsn, client, AUG, now)
    [execution] = aug.executions
    assert execution.trace_id == trace
    assert (execution.prompt_tokens, execution.completion_tokens) == (300, 30)
    # 300 prompt tokens at 1 USD per million, FX 4.0840; the flat model charges no output.
    assert execution.raw_rm == Decimal("0.0012252")
    assert (aug.prompt_tokens, aug.completion_tokens) == (300, 30)
    sep = await _usage(app_dsn, client, SEP, now)
    assert sep.executions == [] and sep.prompt_tokens == 0


async def test_one_trace_is_one_execution_and_its_job_is_the_first_rows(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    multi = uuid.uuid4()
    singles = [uuid.uuid4() for _ in range(3)]
    _ledger(
        migrated_dsn,
        client,
        [
            _row(_kl(2026, 8, 10, 9), multi, job="email_analysis"),
            _row(_kl(2026, 8, 10, 9, 1), multi, job="email_reply"),
            _row(_kl(2026, 8, 10, 9, 2), multi, job="email_reply"),
            _row(_kl(2026, 8, 11), singles[0], job="customer_turn"),
            _row(_kl(2026, 8, 12), singles[1], job="workspace_assistant"),
            _row(_kl(2026, 8, 12), singles[2], job="hr_assistant"),
        ],
    )
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20))
    assert len(usage.executions) == 4
    by_trace = {e.trace_id: e for e in usage.executions}
    assert by_trace[multi].job == "email_analysis"
    assert by_trace[multi].agent_id == "email"
    assert by_trace[multi].prompt_tokens == 3000
    assert {by_trace[t].agent_id for t in singles} == {"support", "assistant", "hr"}
    # Newest first by (first_at, trace_id), the keyset Task 9 paginates by.
    keys = [(e.first_at, e.trace_id) for e in usage.executions]
    assert keys == sorted(keys, reverse=True)
    assert [p.execution for p in usage.priced] == usage.executions
    assert usage.status == "ok" and usage.final is False
    _assert_sums_exact(usage)


async def test_the_tier_steps_down_past_2000_and_reprices_the_whole_open_cycle(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    _bulk(migrated_dsn, client, _kl(2026, 8, 10, 0), 2000)
    now = _kl(2026, 8, 20)
    at_2000 = await _usage(app_dsn, client, AUG, now)
    assert len(at_2000.executions) == 2000
    assert {p.markup for p in at_2000.priced} == {Decimal(75)}
    assert at_2000.bill is not None
    # 2,000 x RM0.004084 x 1.75 = 14.294
    assert at_2000.bill.ai_charge == Decimal("14.29")
    _bulk(migrated_dsn, client, _kl(2026, 8, 19, 0), 1)
    at_2001 = await _usage(app_dsn, client, AUG, now)
    assert len(at_2001.priced) == 2001
    assert {p.markup for p in at_2001.priced} == {Decimal(70)}
    assert at_2001.bill is not None
    # 2,001 x RM0.004084 x 1.70 = 13.8925428: more executions, a lower bill.
    assert at_2001.bill.ai_charge == Decimal("13.89")
    assert at_2001.bill.total == Decimal("99.99") + Decimal("13.89")
    _assert_sums_exact(at_2001)


# --- fee, window and statuses ----------------------------------------------------------------


async def test_zero_usage_join_mid_cycle_bills_the_prorated_fee_only(
    migrated_dsn: str, app_dsn: str
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 15))
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20))
    assert usage.status == "ok"
    assert usage.window == (date(2026, 8, 15), date(2026, 9, 5))
    assert (usage.stage, usage.bundle) == ("pilot", "individual")
    assert usage.bill is not None
    # 99.99 x 21 / 31 = 67.7351...
    assert (usage.bill.platform_fee, usage.bill.ai_charge, usage.bill.total) == (
        Decimal("67.74"),
        Decimal("0.00"),
        Decimal("67.74"),
    )
    assert [d.day for d in usage.daily] == [
        date(2026, 8, 15) + timedelta(days=i) for i in range(21)
    ]
    assert {d.price for d in usage.daily} == {Decimal(0)}
    assert usage.by_agent == [] and usage.priced == [] and usage.executions == []
    assert usage.final is False
    full = await _usage(app_dsn, client, SEP, _kl(2026, 9, 6))
    assert full.bill is not None and full.bill.platform_fee == Decimal("99.99")
    assert full.window == (SEP.start, SEP.end) and len(full.daily) == 30


async def test_usage_before_the_join_date_is_not_billed(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 15))
    billed = uuid.uuid4()
    _ledger(
        migrated_dsn,
        client,
        [_row(_kl(2026, 8, 10)), _row(_kl(2026, 8, 15, 0), billed)],
    )
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20))
    assert [e.trace_id for e in usage.executions] == [billed]
    assert usage.usage_without_plan == 1
    assert usage.daily[0].day == date(2026, 8, 15) and usage.daily[0].price > 0


async def test_not_set_up_has_no_bill_and_counts_the_usage(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    trace = uuid.uuid4()
    _ledger(
        migrated_dsn,
        client,
        [
            _row(_kl(2026, 8, 6), trace),
            _row(_kl(2026, 8, 6, 13), trace),
            _row(_kl(2026, 8, 7)),
            _row(_kl(2026, 8, 8)),
        ],
    )
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20))
    assert usage.status == "not_set_up"
    assert usage.window is None and usage.bill is None
    assert usage.stage is None and usage.bundle is None
    assert usage.executions == [] and usage.priced == []
    assert usage.by_agent == [] and usage.daily == []
    assert usage.usage_without_plan == 3
    with pytest.raises(ValueError, match="timezone"):
        await _usage(app_dsn, client, AUG, datetime(2026, 8, 20))  # noqa: DTZ001


async def test_unpriced_calls_leave_the_cycle_pending_and_are_never_priced_at_zero(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    pending = uuid.uuid4()
    _ledger(
        migrated_dsn,
        client,
        [
            _row(_kl(2026, 8, 10), prompt=1_000_000),
            _row(_kl(2026, 8, 11), prompt=1_000_000),
            _row(_kl(2026, 8, 12), pending, prompt=1_000_000),
            _row(_kl(2026, 8, 12, 13), pending, model=UNPRICED),
        ],
    )
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20))
    assert usage.status == "pending_prices"
    assert usage.unpriced_executions == 1
    by_trace = {e.trace_id: e for e in usage.executions}
    assert len(by_trace) == 3
    assert by_trace[pending].raw_rm is None
    assert by_trace[pending].unpriced_calls == 1
    assert by_trace[pending].prompt_tokens == 1_001_000
    assert usage.price_by_trace.get(pending) is None
    assert len(usage.priced) == 2 and pending not in {p.execution.trace_id for p in usage.priced}
    # The priced part still totals: 2 x RM4.084 x 1.75 = 14.294. The tier counts all three.
    assert usage.raw_cost_rm == Decimal("8.168")
    assert usage.bill is not None and usage.bill.ai_charge == Decimal("14.29")
    assert usage.markup == Decimal(75)
    _assert_sums_exact(usage)


async def test_by_agent_and_daily_sum_exactly_to_the_ai_charge(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "collaborator", "curated", date(2026, 8, 5))
    calls = [
        (_kl(2026, 8, 6, 9), "customer_turn", 1237, 311),
        (_kl(2026, 8, 6, 10), "customer_turn", 977, 129),
        (_kl(2026, 8, 7, 11), "customer_turn", 2203, 17),
        (_kl(2026, 8, 7, 12), "email_analysis", 3331, 401),
        (_kl(2026, 8, 9, 8), "email_compose", 1501, 599),
        (_kl(2026, 8, 9, 9), "hr_assistant", 703, 97),
        (_kl(2026, 8, 9, 23, 59), "training_summary", 4999, 1),
    ]
    rows = [
        _row(at, job=job, provider="groq", model="openai/gpt-oss-120b", prompt=p, completion=c)
        for at, job, p, c in calls
    ]
    _ledger(migrated_dsn, client, rows)
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20))
    _assert_sums_exact(usage)
    assert usage.by_agent == sorted(usage.by_agent, key=lambda a: (-a.price, a.agent_id))
    counts = {a.agent_id: a.executions for a in usage.by_agent}
    assert counts == {"support": 3, "email": 2, "hr": 1, "training": 1}
    titles = {a.agent_id: a.title for a in usage.by_agent}
    assert titles["support"] == "Customer support assistant"
    assert len(usage.daily) == 31
    nonzero = {d.day for d in usage.daily if d.price}
    assert nonzero == {date(2026, 8, 6), date(2026, 8, 7), date(2026, 8, 9)}
    assert all(p.price == p.price.quantize(Decimal("0.0001")) for p in usage.priced)
    assert usage.markup == Decimal(80)


async def test_termination_ends_the_window_and_later_cycles_bill_nothing(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    _plan(migrated_dsn, client, "terminated", "individual", date(2026, 9, 20))
    billed = uuid.uuid4()
    _ledger(
        migrated_dsn,
        client,
        [
            _row(_kl(2026, 9, 10), billed),
            _row(_kl(2026, 9, 20, 0)),  # the termination date itself: not billed
            _row(_kl(2026, 10, 6)),
            _row(_kl(2026, 10, 7)),
        ],
    )
    now = _kl(2026, 10, 10)
    sep = await _usage(app_dsn, client, SEP, now)
    assert sep.status == "ok" and sep.final is True
    assert sep.window == (date(2026, 9, 5), date(2026, 9, 20))
    # The final period is priced on the plan before the termination, prorated: 99.99 x 15 / 30.
    assert (sep.stage, sep.bundle) == ("pilot", "individual")
    assert sep.bill is not None and sep.bill.platform_fee == Decimal("50.00")
    assert [e.trace_id for e in sep.executions] == [billed]
    assert sep.usage_after_termination == 1
    assert len(sep.daily) == 15
    oct_ = await _usage(app_dsn, client, OCT, now)
    assert oct_.status == "ok" and oct_.window is None and oct_.final is False
    assert oct_.bill is not None
    assert (oct_.bill.platform_fee, oct_.bill.ai_charge, oct_.bill.total) == (
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("0.00"),
    )
    assert oct_.executions == [] and oct_.daily == [] and oct_.by_agent == []
    assert oct_.usage_after_termination == 2


async def test_a_fallback_provider_job_is_one_execution_priced_by_both_rows(
    migrated_dsn: str, app_dsn: str
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    trace = uuid.uuid4()
    _ledger(
        migrated_dsn,
        client,
        [
            _row(_kl(2026, 8, 10), trace, provider="groq", model="openai/gpt-oss-120b",
                 prompt=1000, completion=200),
            _row(_kl(2026, 8, 10, 12, 1), trace, provider="gemini",
                 model="gemini-3.1-flash-lite", prompt=1000, completion=200),
        ],
    )  # fmt: skip
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20))
    [execution] = usage.executions
    # groq (1000 x 0.15 + 200 x 0.60) / 1e6 = 0.00027 USD; gemini (1000 x 0.25 + 200 x 1.50) / 1e6
    # = 0.00055 USD; 0.00082 USD x 4.0840 = RM0.00334888.
    assert execution.raw_rm == Decimal("0.00334888")
    assert execution.unpriced_calls == 0
    [priced] = usage.priced
    assert priced.exact_price == Decimal("0.00334888") * Decimal("1.75")
    assert usage.bill is not None and usage.bill.ai_charge == Decimal("0.01")
    assert priced.price == Decimal("0.0100")


async def test_a_company_never_sees_another_companys_usage(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    a, b = _company(migrated_dsn), _company(migrated_dsn)
    for client in (a, b):
        _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    _ledger(migrated_dsn, a, [_row(_kl(2026, 8, 10))])
    _ledger(migrated_dsn, b, [_row(_kl(2026, 8, 10)), _row(_kl(2026, 8, 11))])
    now = _kl(2026, 8, 20)
    assert len((await _usage(app_dsn, a, AUG, now)).executions) == 1
    assert len((await _usage(app_dsn, b, AUG, now)).executions) == 2
    # A connection scoped to A asking for B is a caller bug: refused, not an empty summary.
    with pytest.raises(RuntimeError, match=r"app\.client_id"):
        await _usage_as(app_dsn, a, b, AUG, now)
    with pytest.raises(RuntimeError, match=r"app\.client_id"):
        async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
            async with conn.transaction():
                await list_cycles(conn, uuid.UUID(a), now)


async def test_a_closed_cycle_is_unchanged_by_later_config_rows(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2031, 5, 5))
    _ledger(
        migrated_dsn,
        client,
        [_row(_kl(2031, 5, d), model=STABLE, prompt=1_000_000) for d in (6, 7, 8)]
        + [_row(_kl(2031, 6, 6), model=STABLE, prompt=1_000_000)],
    )
    now = _kl(2031, 6, 10)
    before = await _usage(app_dsn, client, MAY_31, now)
    assert before.final is True
    assert before.bill is not None
    # 3 x RM4.084 x 1.75 = 21.441
    assert (before.bill.platform_fee, before.bill.ai_charge) == (Decimal("99.99"), Decimal("21.44"))
    changed = datetime(2031, 6, 5, tzinfo=KL)
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO billing_fx_rates (usd_to_myr, effective_from) VALUES (5, %s)", (changed,)
        )
        conn.execute(
            "INSERT INTO billing_stage_fees (stage, monthly_fee_rm, effective_from)"
            " VALUES ('pilot', 150, %s)",
            (changed,),
        )
        conn.execute(
            "INSERT INTO ai_model_prices"
            " (provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from)"
            " VALUES (%s, %s, 2, 0, %s)",
            (SYN, STABLE, changed),
        )
        for up_to, pct in ((2000, 50), (10000, 40), (None, 30)):
            conn.execute(
                "INSERT INTO billing_markup_tiers"
                " (stage, bundle, up_to_executions, markup_pct, effective_from)"
                " VALUES ('pilot', 'individual', %s, %s, %s)",
                (up_to, pct, changed),
            )
    after = await _usage(app_dsn, client, MAY_31, now)
    assert after == before
    # The new rows are in force from 5 Jun: 1,000,000 tokens x 2 USD x 5 = RM10, markup 50%.
    june = await _usage(app_dsn, client, JUN_31, now)
    assert june.final is False
    assert june.bill is not None
    assert (june.bill.platform_fee, june.bill.ai_charge) == (Decimal("150.00"), Decimal("15.00"))


async def test_list_cycles_newest_first_with_totals(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _ledger(migrated_dsn, client, [_row(_kl(2026, 7, 20))])  # before any plan
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 15))
    _ledger(migrated_dsn, client, [_row(_kl(2026, 8, 20), prompt=1_000_000)])
    picks = await _cycles(app_dsn, client, _kl(2026, 10, 10))
    assert [p.cycle for p in picks] == [OCT, SEP, AUG, JUL]
    # Aug: fee 67.74 + AI 4.084 x 1.75 = 7.147 -> 7.15.
    assert [p.total for p in picks] == [
        Decimal("99.99"),
        Decimal("99.99"),
        Decimal("74.89"),
        None,
    ]
    assert [p.final for p in picks] == [False, True, True, True]

    nothing = _company(migrated_dsn)
    [only] = await _cycles(app_dsn, nothing, _kl(2026, 10, 10))
    assert (only.cycle, only.total, only.final) == (OCT, None, False)

    plan_only = _company(migrated_dsn)
    _plan(migrated_dsn, plan_only, "collaborator", "complete", date(2026, 10, 5))
    [current] = await _cycles(app_dsn, plan_only, _kl(2026, 10, 10))
    assert (current.cycle, current.total) == (OCT, Decimal("119.99"))
    assert cycle_containing(date(2026, 10, 10)) == OCT


# --- the workbook vectors and a size guard ---------------------------------------------------


async def test_workbook_vector_pilot_individual_1000_executions(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    _bulk(migrated_dsn, client, _kl(2026, 8, 6, 0), 1000, model=VECTOR_1K, prompt=1_000_000)
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 9, 6))
    assert usage.final is True and usage.status == "ok"
    assert len(usage.executions) == 1000
    assert _near(usage.raw_cost_rm, "16.73")
    assert usage.bill is not None
    assert _near(usage.bill.ai_charge, "29.28")
    assert usage.bill.platform_fee == Decimal("99.99")
    assert _near(usage.bill.total, "129.27")
    _assert_sums_exact(usage)


async def test_workbook_vector_collaborator_complete_20000_executions_is_fast(
    migrated_dsn: str, app_dsn: str, prices: None
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "collaborator", "complete", date(2026, 8, 5))
    _bulk(migrated_dsn, client, _kl(2026, 8, 6, 0), 20_000, model=VECTOR_20K, prompt=1_000_000)
    started = time.monotonic()
    usage = await _usage(app_dsn, client, AUG, _kl(2026, 9, 6))
    elapsed = time.monotonic() - started
    assert elapsed < 15, f"cycle_usage took {elapsed:.1f}s for 20,000 executions"
    assert len(usage.executions) == 20_000
    assert _near(usage.raw_cost_rm, "334.73")
    assert usage.bill is not None
    assert _near(usage.bill.ai_charge, "594.15")
    assert usage.bill.platform_fee == Decimal("119.99")
    assert _near(usage.bill.total, "714.14")
    _assert_sums_exact(usage)
