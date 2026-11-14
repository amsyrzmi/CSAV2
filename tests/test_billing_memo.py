"""The closed-cycle memo (``csa.billing.memo``): pure LRU and grace-day rules, then the memo
through ``cycle_usage`` and ``list_cycles`` against the real migrated schema. Synthetic companies,
rows and model names only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast

import psycopg
import pytest
from csa.billing import service
from csa.billing.cycles import Cycle
from csa.billing.memo import UsageMemo, cacheable, fingerprint
from csa.billing.service import CycleUsage, cycle_usage, list_cycles

from tests.conftest import requires_docker
from tests.test_billing_service import AUG, Row, _kl, _ledger, _plan, _row
from tests.test_bookings_schema import _owner
from tests.test_training_schema import _company

SEP = Cycle(date(2026, 9, 5), date(2026, 10, 5))
LATE = "memo-late-price"  # unpriced until a test back-dates a price for it


def _groq(at: datetime) -> Row:
    """A call to the seeded, priced Groq model."""
    return _row(at, provider="groq", model="openai/gpt-oss-120b")


def _fake(executions: int) -> CycleUsage:
    """A stand-in: the memo only reads ``len(usage.executions)``."""
    return cast(CycleUsage, SimpleNamespace(executions=[None] * executions))


def _key(n: int, fp: tuple[Any, ...] = (1,)) -> Any:
    return (uuid.UUID(int=n), date(2026, 8, 5), fp)


# --- pure ------------------------------------------------------------------------------------


def test_cacheable_only_a_full_day_after_the_cycle_closes() -> None:
    assert not cacheable(AUG, _kl(2026, 8, 20))  # open
    assert not cacheable(AUG, _kl(2026, 9, 5, 0, 0))  # closed, inside the grace day
    assert not cacheable(AUG, datetime(2026, 9, 5, 15, 59, 59, tzinfo=UTC))  # 23:59:59 KL
    assert cacheable(AUG, datetime(2026, 9, 5, 16, 0, 0, tzinfo=UTC))  # 6 Sep 00:00 KL
    with pytest.raises(ValueError, match="timezone"):
        cacheable(AUG, datetime(2026, 9, 10))  # noqa: DTZ001


def test_hit_returns_the_same_object_and_a_new_fingerprint_misses() -> None:
    memo = UsageMemo()
    usage = _fake(3)
    memo.put(_key(1), usage)
    assert memo.get(_key(1)) is usage
    assert memo.get(_key(1, (2,))) is None
    assert memo.get(_key(2)) is None
    memo.clear()
    assert len(memo) == 0 and memo.get(_key(1)) is None


def test_least_recently_used_is_evicted_past_the_entry_bound() -> None:
    memo = UsageMemo(max_entries=2)
    a, b, c = _fake(1), _fake(1), _fake(1)
    memo.put(_key(1), a)
    memo.put(_key(2), b)
    assert memo.get(_key(1)) is a  # 1 is now the most recently used
    memo.put(_key(3), c)
    assert len(memo) == 2
    assert memo.get(_key(2)) is None
    assert memo.get(_key(1)) is a and memo.get(_key(3)) is c


def test_the_execution_budget_evicts_and_refuses_an_oversized_cycle() -> None:
    memo = UsageMemo(max_entries=10, max_executions=100)
    memo.put(_key(1), _fake(60))
    memo.put(_key(2), _fake(30))
    memo.put(_key(3), _fake(20))  # 110 held: the oldest goes
    assert memo.get(_key(1)) is None and len(memo) == 2
    memo.put(_key(4), _fake(101))  # bigger than the whole budget: not kept, nothing evicted
    assert memo.get(_key(4)) is None and len(memo) == 2
    memo.put(_key(2), _fake(5))  # replacing an entry frees its old size
    memo.put(_key(5), _fake(75))
    assert len(memo) == 3


# --- through the service ---------------------------------------------------------------------


async def _usage(
    app_dsn: str, client: str, cycle: Cycle, now: datetime, memo: UsageMemo, scope: str = ""
) -> CycleUsage:
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (scope or client,))
            return await cycle_usage(conn, uuid.UUID(client), cycle, now=now, memo=memo)


@requires_docker
async def test_a_closed_cycle_is_memoised_after_its_grace_day_only(
    migrated_dsn: str, app_dsn: str
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    _ledger(migrated_dsn, client, [_groq(_kl(2026, 8, 10))])
    memo = UsageMemo()
    open_ = await _usage(app_dsn, client, AUG, _kl(2026, 8, 20), memo)
    assert open_.final is False and len(memo) == 0
    grace = await _usage(app_dsn, client, AUG, _kl(2026, 9, 5, 12), memo)
    assert grace.final is True and len(memo) == 0
    first = await _usage(app_dsn, client, AUG, _kl(2026, 9, 6), memo)
    again = await _usage(app_dsn, client, AUG, _kl(2026, 9, 20), memo)
    assert len(memo) == 1 and again is first
    assert first.bill is not None and first.bill == grace.bill


@requires_docker
async def test_a_back_dated_price_changes_the_fingerprint_and_reprices_a_pending_cycle(
    migrated_dsn: str, app_dsn: str
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    _ledger(
        migrated_dsn,
        client,
        [_groq(_kl(2026, 8, 10)), _row(_kl(2026, 8, 11), provider="synthetic", model=LATE)],
    )
    memo = UsageMemo()
    now = _kl(2026, 10, 1)
    pending = await _usage(app_dsn, client, AUG, now, memo)
    assert pending.status == "pending_prices" and len(memo) == 1
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (client,))
            before = await fingerprint(conn, uuid.UUID(client))
    with _owner(migrated_dsn) as conn:
        conn.execute(
            "INSERT INTO ai_model_prices"
            " (provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from)"
            " VALUES ('synthetic', %s, 1, 1, %s)",
            (LATE, datetime(2020, 1, 1, tzinfo=UTC)),
        )
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.client_id', %s, true)", (client,))
            assert await fingerprint(conn, uuid.UUID(client)) != before
    priced = await _usage(app_dsn, client, AUG, now, memo)
    assert priced is not pending and priced.status == "ok" and len(memo) == 2
    assert pending.bill is not None and priced.bill is not None
    assert priced.bill.ai_charge > pending.bill.ai_charge


@requires_docker
async def test_the_tenant_guard_runs_before_the_memo(migrated_dsn: str, app_dsn: str) -> None:
    client, other = _company(migrated_dsn), _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    memo = UsageMemo()
    await _usage(app_dsn, client, AUG, _kl(2026, 9, 10), memo)
    assert len(memo) == 1
    with pytest.raises(RuntimeError, match="app.client_id"):
        await _usage(app_dsn, client, AUG, _kl(2026, 9, 10), memo, scope=other)


@requires_docker
async def test_list_cycles_computes_closed_cycles_once_and_the_open_cycle_every_time(
    migrated_dsn: str, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _company(migrated_dsn)
    _plan(migrated_dsn, client, "pilot", "individual", date(2026, 8, 5))
    _ledger(migrated_dsn, client, [_groq(_kl(2026, 8, 10)), _groq(_kl(2026, 9, 10))])
    computed: list[date] = []
    real = service._cycle_usage

    async def counting(*args: Any, **kwargs: Any) -> CycleUsage:
        usage = await real(*args, **kwargs)
        computed.append(usage.cycle.start)
        return usage

    monkeypatch.setattr(service, "_cycle_usage", counting)
    memo = UsageMemo()
    now = _kl(2026, 10, 10)  # AUG and SEP closed long ago, OCT open

    async def picks() -> list[Any]:
        async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.client_id', %s, true)", (client,))
                return await list_cycles(conn, uuid.UUID(client), now, memo=memo)

    first = await picks()
    assert sorted(computed) == [AUG.start, SEP.start, date(2026, 10, 5)]
    computed.clear()
    second = await picks()
    assert computed == [date(2026, 10, 5)]
    assert [(p.cycle, p.total, p.final) for p in second] == [
        (p.cycle, p.total, p.final) for p in first
    ]
    # The memo the cycle picker fills serves the usage page too.
    computed.clear()
    sep = await _usage(app_dsn, client, SEP, now, memo)
    assert computed == [] and sep.cycle == SEP
