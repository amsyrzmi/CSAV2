"""LG04 acceptance: coordinator wired into the durable worker (real Postgres)."""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from apps.worker.main import IngressWorker
from csa.contracts.plan import Intent, Plan, Task
from csa.contracts.state import (
    Context,
    Draft,
    GateVerdict,
    GuardResult,
    Signals,
    TurnState,
    Verification,
)
from csa.core import db
from csa.graph.coordinator import build_runtime
from csa.graph.saver import build_checkpoint_pool, build_saver
from csa.turn.deps import FALLBACK_TEXT, PostgresTurnDependencies, _supported_locale

from tests.conftest import owner_dsn_for, requires_docker
from tests.test_worker import _configure_test_settings, _seed_events

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.usefixtures("lg_setup_dsn")]


class Crash(BaseException):
    """Simulates process death; graph nodes only catch Exception."""


class SpyDeps(PostgresTurnDependencies):
    def __init__(self, pool: Any, *, fail_stage: str | None = None, crash: bool = False) -> None:
        super().__init__(pool)
        self.fail_stage = fail_stage
        self.crash = crash
        self.spokes: list[str] = []

    def _maybe_fail(self, stage: str) -> None:
        if self.fail_stage == stage:
            raise RuntimeError(f"synthetic {stage} failure")

    async def load_context(self, envelope: Any) -> Context:
        self._maybe_fail("load_context")
        return await super().load_context(envelope)

    async def guard(self, envelope: Any, context: Context) -> GuardResult:
        self._maybe_fail("guard")
        return await super().guard(envelope, context)

    async def signals(self, envelope: Any, context: Context) -> Signals:
        self._maybe_fail("signals")
        if self.fail_stage == "plan_args":
            return Signals(
                intent=Intent.ORDER,
                capability_scores={"order_agent": 0.9},
                provider="policy",
                version="test",
            )
        return await super().signals(envelope, context)

    async def plan_args(self, envelope: Any, context: Context, signals: Signals) -> Plan:
        self._maybe_fail("plan_args")
        return await super().plan_args(envelope, context, signals)

    async def execute_spoke(self, task: Task, envelope: Any, context: Context) -> Any:
        self.spokes.append(task.agent)
        return await super().execute_spoke(task, envelope, context)

    async def compose(self, state: TurnState) -> Draft:
        if self.crash:
            raise Crash
        self._maybe_fail("compose")
        return await super().compose(state)

    async def verify(self, state: TurnState) -> Verification:
        self._maybe_fail("verify")
        return await super().verify(state)

    async def gate(self, state: TurnState) -> GateVerdict:
        self._maybe_fail("gate")
        return await super().gate(state)

    async def hold(self, state: TurnState) -> None:
        return None

    async def emit(self, state: TurnState) -> None:
        self._maybe_fail("emit")
        await super().emit(state)

    async def finalize(self, state: TurnState) -> None:
        self._maybe_fail("finalize")
        await super().finalize(state)


async def _worker(
    owner_dsn: str,
    app_dsn: str,
    **deps_kwargs: Any,
) -> tuple[Any, Any, Any, IngressWorker, SpyDeps]:
    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    checkpoint_pool = build_checkpoint_pool(app_dsn)
    for pool in (owner_pool, app_pool, checkpoint_pool):
        await pool.open(wait=True)
    deps = SpyDeps(app_pool, **deps_kwargs)
    runtime = build_runtime(deps, build_saver(checkpoint_pool))
    worker = IngressWorker(owner_pool, app_pool, lock_conninfo=owner_dsn, coordinator=runtime)
    return owner_pool, app_pool, checkpoint_pool, worker, deps


async def _close(*pools: Any) -> None:
    for pool in pools:
        await pool.close()


def _requeue(owner_dsn: str, event_id: Any) -> None:
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE ingress_events SET status='received', completed_at=NULL, "
            "next_attempt_at=now() WHERE id=%s",
            (event_id,),
        )


def _counts(owner_dsn: str, client_id: str) -> dict[str, Any]:
    with psycopg.connect(owner_dsn) as conn:
        outbound = conn.execute(
            "SELECT body FROM messages WHERE client_id=%s AND direction='outbound'",
            (client_id,),
        ).fetchall()
        inbound = conn.execute(
            "SELECT count(*) FROM messages WHERE client_id=%s AND direction='inbound'",
            (client_id,),
        ).fetchone()
        deliveries = conn.execute(
            "SELECT count(*) FROM outbound_deliveries WHERE client_id=%s", (client_id,)
        ).fetchone()
        escalations = conn.execute(
            "SELECT reason FROM escalations WHERE client_id=%s", (client_id,)
        ).fetchall()
        status = conn.execute(
            "SELECT status FROM ingress_events WHERE client_id=%s", (client_id,)
        ).fetchone()
    assert inbound is not None and deliveries is not None and status is not None
    return {
        "outbound": [row[0] for row in outbound],
        "inbound": inbound[0],
        "deliveries": deliveries[0],
        "escalations": [row[0] for row in escalations],
        "status": status[0],
    }


async def test_coordinator_turn_persists_once_and_replay_adds_no_rows(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn)
    pools = await _worker(owner_dsn, app_dsn)
    worker = pools[3]
    try:
        assert await worker.process_once() is True
        first = _counts(owner_dsn, client_id)
        assert first["outbound"] == ["Echo: message-1"]
        assert first["inbound"] == 1
        assert first["deliveries"] == 1
        assert first["escalations"] == []
        assert first["status"] == "completed"

        _requeue(owner_dsn, event_ids[0])
        assert await worker.process_once() is True
        assert _counts(owner_dsn, client_id) == first
    finally:
        await _close(*pools[:3])


async def test_finalize_counts_turn_and_persists_supported_locale(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, _ = _seed_events(owner_dsn)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute("UPDATE contacts SET locale='ms' WHERE client_id=%s", (client_id,))
    pools = await _worker(owner_dsn, app_dsn)
    worker = pools[3]
    try:
        assert await worker.process_once() is True
        with psycopg.connect(owner_dsn) as conn:
            turns = conn.execute(
                "SELECT turn_count FROM conversations WHERE client_id=%s", (client_id,)
            ).fetchone()
            locale = conn.execute(
                "SELECT locale FROM contacts WHERE client_id=%s", (client_id,)
            ).fetchone()
            redacted = conn.execute(
                "SELECT body = redacted_body FROM messages "
                "WHERE client_id=%s AND direction='outbound'",
                (client_id,),
            ).fetchone()
        assert turns == (1,)
        assert locale == ("en",)
        assert redacted == (True,)
    finally:
        await _close(*pools[:3])


async def test_client_shadow_policy_suppresses_the_reply_the_default_policy_sends(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE clients SET config = jsonb_set(coalesce(config,'{}'::jsonb), "
            "'{policy}', '{\"deployment_mode\": \"shadow\"}') WHERE id=%s",
            (client_id,),
        )
    pools = await _worker(owner_dsn, app_dsn)
    worker = pools[3]
    try:
        assert await worker.process_once() is True
        shadow = _counts(owner_dsn, client_id)
        assert shadow["deliveries"] == 0
        assert shadow["outbound"] == []

        assert shadow["status"] == "completed"
    finally:
        await _close(*pools[:3])


async def test_supported_locale_normalizes_and_rejects_unsupported() -> None:
    assert _supported_locale("zh-CN") == "zh"
    assert _supported_locale("MS_my") == "ms"
    assert _supported_locale("fr") is None
    assert _supported_locale("de-DE") is None


async def test_kill_between_collect_and_compose_resumes_without_duplicates(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE ingress_events SET raw = jsonb_set(jsonb_set(raw, "
            "'{envelope,message,body_raw}', '\"I will call my lawyer\"'), "
            "'{envelope,message,body_redacted}', '\"I will call my lawyer\"') "
            "WHERE client_id=%s",
            (client_id,),
        )

    pools = await _worker(owner_dsn, app_dsn, crash=True)
    try:
        with pytest.raises(Crash):
            await pools[3].process_once()
        assert pools[4].spokes == ["escalation_agent"]
        crashed = _counts(owner_dsn, client_id)
        assert crashed["outbound"] == []
        assert len(crashed["escalations"]) == 1
    finally:
        await _close(*pools[:3])

    _requeue(owner_dsn, event_ids[0])
    pools = await _worker(owner_dsn, app_dsn)
    try:
        assert await pools[3].process_once() is True
        assert pools[4].spokes == []
        done = _counts(owner_dsn, client_id)
        assert len(done["outbound"]) == 1
        assert done["inbound"] == 1
        assert done["deliveries"] == 1
        assert len(done["escalations"]) == 1
        assert done["status"] == "completed"
    finally:
        await _close(*pools[:3])


@pytest.mark.parametrize(
    "stage",
    [
        "load_context",
        "guard",
        "signals",
        "plan_args",
        "compose",
        "verify",
        "gate",
        "emit",
    ],
)
async def test_forced_exception_ends_in_fail_safe_with_outbound_and_escalation(
    stage: str, pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, _ = _seed_events(owner_dsn)
    pools = await _worker(owner_dsn, app_dsn, fail_stage=stage)
    try:
        assert await pools[3].process_once() is True
        result = _counts(owner_dsn, client_id)
        assert result["outbound"] == [FALLBACK_TEXT]
        assert result["deliveries"] == 1
        assert len(result["escalations"]) == 1
        assert result["escalations"][0] == f"fail_safe:{_code(stage)}:RuntimeError"
        assert result["status"] == "completed"
    finally:
        await _close(*pools[:3])


def _code(stage: str) -> str:
    return {"load_context": "load_ctx"}.get(stage, stage)


async def test_finalize_failure_retries_from_checkpoint_without_second_reply(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    owner_dsn, client_id, event_ids = _seed_events(owner_dsn)

    pools = await _worker(owner_dsn, app_dsn, fail_stage="finalize")
    try:
        assert await pools[3].process_once() is True
        failed = _counts(owner_dsn, client_id)
        assert failed["outbound"] == ["Echo: message-1"]
        assert failed["escalations"] == []
        assert failed["status"] == "failed"
    finally:
        await _close(*pools[:3])

    _requeue(owner_dsn, event_ids[0])
    pools = await _worker(owner_dsn, app_dsn)
    try:
        assert await pools[3].process_once() is True
        done = _counts(owner_dsn, client_id)
        assert done["outbound"] == ["Echo: message-1"]
        assert done["deliveries"] == 1
        assert done["status"] == "completed"
    finally:
        await _close(*pools[:3])
