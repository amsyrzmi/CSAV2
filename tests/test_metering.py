"""The metering package: the bound context, the job maps and the ledger writer.

The writer tests that need a database run on the disposable Docker Postgres; the failure tests
use a pool that cannot connect. All data is synthetic.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, LiteralString, get_args
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from csa.core.db import build_pool
from csa.core.tenancy import tx
from csa.metering import meter as meter_module
from csa.metering.context import (
    JOB_AGENT,
    JOB_LABELS,
    Job,
    MeterContext,
    MeteringContextMissing,
    current_context,
    meter_context,
    meter_context_unless_bound,
)
from csa.metering.meter import LedgerMeter, NullMeter, Usage
from psycopg_pool import AsyncConnectionPool
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_training_schema import _company

ROOT = Path(__file__).resolve().parent.parent
USAGE = Usage(
    provider="groq",
    model="synthetic-model",
    prompt_tokens=120,
    completion_tokens=30,
    schema_name="SyntheticReply",
)


def _ctx(client_id: UUID | None = None, job: Job = "customer_turn") -> MeterContext:
    return MeterContext(client_id=client_id or uuid4(), trace_id=uuid4(), job=job)


class RecordingMirror:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[MeterContext, Usage]] = []
        self.error = error

    def submit(self, ctx: MeterContext, usage: Usage) -> None:
        self.calls.append((ctx, usage))
        if self.error is not None:
            raise self.error


class DeadPool:
    """A pool whose connections always fail, counting the attempts."""

    def __init__(self) -> None:
        self.attempts = 0

    def connection(self) -> Any:
        self.attempts += 1
        raise psycopg.OperationalError("synthetic outage")


class _ScriptedConn:
    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, sql: Any, params: Any = None) -> None:
        return None

    @asynccontextmanager
    async def cursor(self) -> AsyncIterator[_ScriptedConn]:
        yield self


class ScriptedPool:
    """A pool whose attempts follow a script: "fail" raises, "stall" hangs, "ok" inserts."""

    def __init__(self, *script: str) -> None:
        self.script = list(script)
        self.attempts = 0
        self.inserts: list[Any] = []

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[_ScriptedConn]:
        step = self.script[self.attempts]
        self.attempts += 1
        if step == "fail":
            raise psycopg.OperationalError("synthetic outage")
        if step == "stall":
            await asyncio.sleep(60)
        yield _ScriptedConn()
        self.inserts.append(step)


def _dead(mirror: RecordingMirror | None = None) -> tuple[LedgerMeter, DeadPool]:
    pool = DeadPool()
    return LedgerMeter(pool, mirror), pool  # type: ignore[arg-type]


def test_context_missing_raises() -> None:
    with pytest.raises(MeteringContextMissing):
        current_context()
    with pytest.raises(MeteringContextMissing):
        LedgerMeter(DeadPool()).require_context()  # type: ignore[arg-type]
    # The evals-only no-op meter never needs a context.
    NullMeter().require_context()


def test_context_binds_and_nests_and_resets() -> None:
    outer, inner = _ctx(job="customer_turn"), _ctx(job="email_reply")
    with meter_context(outer):
        assert current_context() is outer
        with meter_context(inner):
            assert current_context() is inner
        assert current_context() is outer
    with pytest.raises(MeteringContextMissing):
        current_context()


def test_context_resets_when_the_body_raises() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with meter_context(_ctx()):
            raise RuntimeError("boom")
    with pytest.raises(MeteringContextMissing):
        current_context()


def test_unless_bound_binds_when_nothing_is_bound_and_resets() -> None:
    own = _ctx(job="email_analysis")
    with meter_context_unless_bound(own):
        assert current_context() is own
    with pytest.raises(MeteringContextMissing):
        current_context()


def test_unless_bound_inherits_an_enclosing_binding() -> None:
    outer, own = _ctx(job="workspace_assistant"), _ctx(job="email_compose")
    with meter_context(outer):
        with meter_context_unless_bound(own):
            assert current_context() is outer
            # A plain binding inside still nests and resets to the inherited one.
            inner = _ctx(job="customer_turn")
            with meter_context(inner):
                assert current_context() is inner
            assert current_context() is outer
        assert current_context() is outer
    with pytest.raises(MeteringContextMissing):
        current_context()


def test_unless_bound_resets_when_the_body_raises() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with meter_context_unless_bound(_ctx()):
            raise RuntimeError("boom")
    with pytest.raises(MeteringContextMissing):
        current_context()
    outer = _ctx()
    with meter_context(outer):
        with pytest.raises(RuntimeError, match="boom"):
            with meter_context_unless_bound(_ctx()):
                raise RuntimeError("boom")
        assert current_context() is outer


def test_job_maps_cover_closed_set() -> None:
    jobs = set(get_args(Job))
    assert set(JOB_LABELS) == jobs
    assert set(JOB_AGENT) == jobs
    migration = (ROOT / "db" / "migrations" / "0570_ai_usage_events.sql").read_text("utf-8")
    [check] = re.findall(r"job IN \(([^)]*)\)", migration)
    assert set(re.findall(r"'([a-z_]+)'", check)) == jobs
    assert JOB_LABELS["customer_turn"] == "Customer reply"
    assert JOB_AGENT["customer_turn"] == "support"
    assert {JOB_AGENT[j] for j in ("email_analysis", "email_reply", "email_compose")} == {"email"}


async def test_record_without_context_raises_and_does_not_touch_the_pool() -> None:
    meter, pool = _dead()
    with pytest.raises(MeteringContextMissing):
        await meter.record(USAGE)
    assert pool.attempts == 0


async def test_null_meter_records_nothing() -> None:
    await NullMeter().record(USAGE)


async def test_ledger_failure_retries_once_then_logs_and_returns() -> None:
    mirror = RecordingMirror()
    meter, pool = _dead(mirror)
    ctx = MeterContext(client_id=uuid4(), trace_id=uuid4(), job="hr_assistant")
    with capture_logs() as logs, meter_context(ctx):
        await meter.record(USAGE)
    assert pool.attempts == 2
    [event] = [e for e in logs if e["event"] == "ai_usage_write_failed"]
    assert event["log_level"] == "error"
    assert event["client_id"] == str(ctx.client_id)
    assert event["trace_id"] == str(ctx.trace_id)
    assert event["job"] == "hr_assistant"
    assert event["provider"] == "groq"
    assert event["model"] == "synthetic-model"
    assert event["prompt_tokens"] == 120
    assert event["completion_tokens"] == 30
    # Counts and identifiers only: no text of any kind.
    assert event["schema_name"] == "SyntheticReply"
    assert event["conversation_id"] is None
    assert event["error_type"] == "OperationalError"
    assert set(event) == {
        "event",
        "log_level",
        "client_id",
        "trace_id",
        "conversation_id",
        "job",
        "provider",
        "model",
        "prompt_tokens",
        "completion_tokens",
        "schema_name",
        "error_type",
    }
    # The type name only, never the message (which may carry a DSN).
    assert "synthetic outage" not in str(event)
    assert mirror.calls == [(ctx, USAGE)]


async def test_ledger_first_attempt_fails_then_second_writes_one_row() -> None:
    pool = ScriptedPool("fail", "ok")
    meter = LedgerMeter(pool)  # type: ignore[arg-type]
    with capture_logs() as logs, meter_context(_ctx()):
        await meter.record(USAGE)
    assert pool.attempts == 2
    assert pool.inserts == ["ok"]
    assert [e for e in logs if e["log_level"] == "error"] == []


async def test_ledger_stall_is_bounded_retried_then_logged_and_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meter_module, "WRITE_ATTEMPT_TIMEOUT_S", 0.01)
    mirror = RecordingMirror()
    pool = ScriptedPool("stall", "stall")
    meter = LedgerMeter(pool, mirror)  # type: ignore[arg-type]
    conversation = uuid4()
    ctx = MeterContext(
        client_id=uuid4(), trace_id=uuid4(), job="email_reply", conversation_id=conversation
    )
    with capture_logs() as logs, meter_context(ctx):
        async with asyncio.timeout(5):  # the test fails rather than hangs if unbounded
            await meter.record(USAGE)
    assert pool.attempts == 2
    assert pool.inserts == []
    [event] = [e for e in logs if e["event"] == "ai_usage_write_failed"]
    assert event["log_level"] == "error"
    assert event["conversation_id"] == str(conversation)
    assert event["schema_name"] == "SyntheticReply"
    assert event["error_type"] == "TimeoutError"
    assert mirror.calls == [(ctx, USAGE)]


async def test_ledger_stall_then_success_writes_one_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(meter_module, "WRITE_ATTEMPT_TIMEOUT_S", 0.01)
    pool = ScriptedPool("stall", "ok")
    meter = LedgerMeter(pool)  # type: ignore[arg-type]
    with capture_logs() as logs, meter_context(_ctx()):
        await meter.record(USAGE)
    assert pool.inserts == ["ok"]
    assert [e for e in logs if e["log_level"] == "error"] == []


async def test_outer_cancellation_still_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # The caller's own deadline expiring mid-write must cancel the turn, not be swallowed.
    monkeypatch.setattr(meter_module, "WRITE_ATTEMPT_TIMEOUT_S", 30)
    pool = ScriptedPool("stall", "stall")
    meter = LedgerMeter(pool)  # type: ignore[arg-type]
    with meter_context(_ctx()), pytest.raises(TimeoutError):
        async with asyncio.timeout(0.01):
            await meter.record(USAGE)
    assert pool.attempts == 1


async def test_mirror_exception_is_never_raised() -> None:
    mirror = RecordingMirror(error=RuntimeError("langfuse down"))
    meter, _ = _dead(mirror)
    with capture_logs() as logs, meter_context(_ctx()):
        await meter.record(USAGE)
    assert len(mirror.calls) == 1
    assert [e["event"] for e in logs if e["log_level"] == "error"] == ["ai_usage_write_failed"]
    assert any(e["event"] == "ai_usage_mirror_failed" for e in logs)


@pytest_asyncio.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = build_pool(app_dsn, min_size=1, max_size=3)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


async def _rows(pool: AsyncConnectionPool, client_id: str) -> list[dict[str, Any]]:
    select: LiteralString = (
        "SELECT client_id, trace_id, conversation_id, job, provider, model, prompt_tokens,"
        " completion_tokens, schema_name FROM ai_usage_events WHERE client_id = %s"
    )
    async with tx(pool, client_id) as conn:
        cur = await conn.execute(select, (client_id,))
        return await cur.fetchall()


@requires_docker
async def test_ledger_meter_writes_one_row(migrated_dsn: str, pool: AsyncConnectionPool) -> None:
    client = _company(migrated_dsn)
    mirror = RecordingMirror()
    conversation = uuid4()
    ctx = MeterContext(
        client_id=UUID(client), trace_id=uuid4(), job="email_reply", conversation_id=conversation
    )
    with meter_context(ctx):
        await LedgerMeter(pool, mirror).record(USAGE)
    assert await _rows(pool, client) == [
        {
            "client_id": UUID(client),
            "trace_id": ctx.trace_id,
            "conversation_id": conversation,
            "job": "email_reply",
            "provider": "groq",
            "model": "synthetic-model",
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "schema_name": "SyntheticReply",
        }
    ]
    assert mirror.calls == [(ctx, USAGE)]


@requires_docker
async def test_ledger_write_uses_its_own_transaction(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    client = _company(migrated_dsn)
    ctx = _ctx(UUID(client))
    meter = LedgerMeter(pool)
    with pytest.raises(RuntimeError, match="caller failed"):
        async with tx(pool, client):
            with meter_context(ctx):
                await meter.record(USAGE)
            raise RuntimeError("caller failed")
    [row] = await _rows(pool, client)
    assert row["trace_id"] == ctx.trace_id


@requires_docker
async def test_ledger_failure_for_an_unknown_company_is_logged_not_raised(
    migrated_dsn: str, pool: AsyncConnectionPool
) -> None:
    # The foreign key refuses a company that does not exist: a real write failure end to end.
    assert migrated_dsn
    ctx = _ctx()
    with capture_logs() as logs, meter_context(ctx):
        await LedgerMeter(pool).record(USAGE)
    assert [e["event"] for e in logs if e["log_level"] == "error"] == ["ai_usage_write_failed"]
