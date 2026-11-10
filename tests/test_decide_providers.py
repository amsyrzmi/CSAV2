from __future__ import annotations

import uuid

import psycopg
import pytest
from csa.contracts.envelope import Envelope
from csa.contracts.plan import Intent
from csa.contracts.state import Context, Signals
from csa.core import db
from csa.core.tenancy import tx
from csa.decide.jev import JevError
from csa.decide.providers import DecisionRecord, classify_with
from csa.graph.policy.gate import Policy
from csa.ops.runbooks import force_decide_mode
from csa.turn.deps import PostgresTurnDependencies
from psycopg.types.json import Jsonb

from tests.conftest import requires_docker
from tests.test_coordinator import make_envelope


class FakeClassifier:
    def __init__(
        self,
        provider: str,
        intent: Intent = Intent.ORDER,
        confidence: float | None = 0.9,
        error: Exception | None = None,
    ) -> None:
        self.provider = provider
        self.intent = intent
        self.confidence = confidence
        self.error = error
        self.calls = 0

    @property
    def version(self) -> str:
        return f"{self.provider}:test"

    async def classify(self, envelope: Envelope, ctx: Context) -> Signals:
        self.calls += 1
        if self.error is not None:
            raise self.error
        answers: dict[str, bool | float | str | None] = {
            "language": "en",
            "wants_human": False,
            "injection": False,
            "urgent": False,
        }
        if self.confidence is not None:
            answers["intent_confidence"] = self.confidence
        return Signals(
            intent=self.intent,
            answers=answers,
            provider="jev" if self.provider == "jev" else "llm",
            version=self.version,
        )


async def _run(mode, llm, jev, min_confidence=0.5):  # noqa: ANN001, ANN202
    return await classify_with(
        mode,
        make_envelope("i want 2 A1"),
        Context(),
        llm=llm,
        jev=jev,
        min_confidence=min_confidence,
    )


async def test_llm_mode_skips_jev_and_records_nothing() -> None:
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev")
    signals, record = await _run("llm", llm, jev)
    assert signals.provider == "llm"
    assert record is None
    assert jev.calls == 0


async def test_non_llm_mode_requires_jev() -> None:
    with pytest.raises(RuntimeError, match="jev_not_configured"):
        await _run("cascade", FakeClassifier("llm"), None)


async def test_shadow_uses_llm_and_logs_disagreement() -> None:
    llm = FakeClassifier("llm", intent=Intent.ORDER)
    jev = FakeClassifier("jev", intent=Intent.PRODUCT_INFO)
    signals, record = await _run("shadow", llm, jev)
    assert signals.provider == "llm"
    assert record is not None
    assert record.provider == "llm"
    assert record.answers["jev"] is not None
    assert record.disagree == ["intent"]
    assert record.fallback_reason is None


async def test_shadow_records_jev_error_without_failing() -> None:
    jev = FakeClassifier("jev", error=JevError("jev_timeout"))
    signals, record = await _run("shadow", FakeClassifier("llm"), jev)
    assert signals.provider == "llm"
    assert record is not None
    assert record.answers["jev"] is None
    assert record.fallback_reason == "jev_timeout"


async def test_cascade_uses_confident_jev_without_calling_llm() -> None:
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev", confidence=0.8)
    signals, record = await _run("cascade", llm, jev)
    assert signals.provider == "jev"
    assert record is not None
    assert record.provider == "jev"
    assert llm.calls == 0


@pytest.mark.parametrize(
    ("jev", "reason"),
    [
        (FakeClassifier("jev", confidence=0.2), "low_intent_confidence"),
        (FakeClassifier("jev", confidence=None), "low_intent_confidence"),
        (FakeClassifier("jev", error=JevError("jev_http_500")), "jev_http_500"),
    ],
)
async def test_cascade_falls_back_to_llm(jev: FakeClassifier, reason: str) -> None:
    llm = FakeClassifier("llm")
    signals, record = await _run("cascade", llm, jev)
    assert signals.provider == "llm"
    assert record is not None
    assert record.mode == "cascade"
    assert record.provider == "llm"
    assert record.fallback_reason == reason
    assert llm.calls == 1


async def test_jev_mode_raises_on_error_and_ignores_confidence() -> None:
    llm = FakeClassifier("llm")
    with pytest.raises(JevError):
        await _run("jev", llm, FakeClassifier("jev", error=JevError("jev_timeout")))
    signals, record = await _run("jev", llm, FakeClassifier("jev", confidence=0.1))
    assert signals.provider == "jev"
    assert record is not None
    assert record.mode == "jev"
    assert llm.calls == 0


@requires_docker
@pytest.mark.usefixtures("app_dsn")
async def test_signals_shadow_mode_writes_decision_log(migrated_dsn: str, app_dsn: str) -> None:
    client_id = uuid.uuid4()
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version, config) "
            "VALUES (%s, %s, 'Synthetic Decide Test', 'lg08-test', %s)",
            (client_id, f"decide-{client_id}", Jsonb({"policy": {"decide_mode": "shadow"}})),
        )
    pool = db.build_pool(app_dsn, max_size=2)
    await pool.open(wait=True)
    try:
        deps = PostgresTurnDependencies(
            pool,
            FakeClassifier("llm", intent=Intent.ORDER),  # type: ignore[arg-type]
            FakeClassifier("jev", error=JevError("jev_timeout")),  # type: ignore[arg-type]
        )
        envelope = make_envelope("i want 2 A1").model_copy(update={"client_id": client_id})
        signals = await deps.signals(envelope, Context())
    finally:
        await pool.close()
    assert signals.provider == "llm"
    with psycopg.connect(migrated_dsn) as conn:
        row = conn.execute(
            "SELECT mode, provider, fallback_reason, final->>'intent' FROM decision_log "
            "WHERE client_id=%s",
            (client_id,),
        ).fetchone()
    assert row == ("shadow", "llm", "jev_timeout", "order")


# Customer classifier wiring: CUSTOMER_DECIDE_MODE_DEFAULT applies only to a tenant with no stored
# decide_mode and only when the worker has a Jev provider.


def _customer_deps(
    stored_policy: dict[str, object],
    *,
    llm: FakeClassifier,
    jev: FakeClassifier | None,
    default: str,
) -> tuple[PostgresTurnDependencies, list[DecisionRecord]]:
    deps = PostgresTurnDependencies(
        None,  # type: ignore[arg-type]
        llm,  # type: ignore[arg-type]
        jev,  # type: ignore[arg-type]
        default_decide_mode=default,  # type: ignore[arg-type]
    )
    records: list[DecisionRecord] = []

    async def policy(client_id: uuid.UUID) -> Policy:
        return Policy.model_validate(stored_policy)

    async def log_decision(envelope: Envelope, record: DecisionRecord) -> None:
        records.append(record)

    deps._policy = policy  # type: ignore[method-assign]
    deps._log_decision = log_decision  # type: ignore[method-assign]
    return deps, records


async def test_tenant_explicit_llm_mode_wins() -> None:
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev")
    deps, records = _customer_deps({"decide_mode": "llm"}, llm=llm, jev=jev, default="cascade")
    signals = await deps.signals(make_envelope("i want 2 A1"), Context())
    assert signals.provider == "llm"
    assert jev.calls == 0
    assert records == []


async def test_tenant_explicit_shadow_mode_wins_over_the_llm_default() -> None:
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev")
    deps, records = _customer_deps({"decide_mode": "shadow"}, llm=llm, jev=jev, default="llm")
    await deps.signals(make_envelope("i want 2 A1"), Context())
    assert jev.calls == 1
    assert [r.mode for r in records] == ["shadow"]


async def test_no_client_means_llm_whatever_the_default() -> None:
    llm = FakeClassifier("llm")
    deps, records = _customer_deps(
        {"deployment_mode": "auto"}, llm=llm, jev=None, default="cascade"
    )
    signals = await deps.signals(make_envelope("i want 2 A1"), Context())
    assert signals.provider == "llm"
    assert llm.calls == 1
    assert records == []


async def test_default_llm_changes_nothing() -> None:
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev")
    deps, records = _customer_deps({}, llm=llm, jev=jev, default="llm")
    signals = await deps.signals(make_envelope("i want 2 A1"), Context())
    assert signals.provider == "llm"
    assert jev.calls == 0
    assert records == []


async def test_the_default_decide_mode_is_llm() -> None:
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev")
    deps = PostgresTurnDependencies(None, llm, jev)  # type: ignore[arg-type]

    async def policy(client_id: uuid.UUID) -> Policy:
        return Policy()

    deps._policy = policy  # type: ignore[method-assign]
    await deps.signals(make_envelope("i want 2 A1"), Context())
    assert jev.calls == 0


async def test_jev_error_falls_back_to_the_llm_in_cascade() -> None:
    llm = FakeClassifier("llm")
    jev = FakeClassifier("jev", error=JevError("jev_timeout"))
    deps, records = _customer_deps({}, llm=llm, jev=jev, default="cascade")
    signals = await deps.signals(make_envelope("i want 2 A1"), Context())
    assert signals.provider == "llm"
    assert llm.calls == 1
    assert [(r.mode, r.provider, r.fallback_reason) for r in records] == [
        ("cascade", "llm", "jev_timeout")
    ]


def _seed_customer_client(migrated_dsn: str, config: dict[str, object]) -> uuid.UUID:
    client_id = uuid.uuid4()
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version, config) "
            "VALUES (%s, %s, 'Synthetic Decide Test', 'lg08-test', %s)",
            (client_id, f"decide-{client_id}", Jsonb(config)),
        )
    return client_id


def _decision_rows(migrated_dsn: str, client_id: uuid.UUID) -> list[tuple[object, ...]]:
    with psycopg.connect(migrated_dsn) as conn:
        return conn.execute(
            "SELECT mode, provider, fallback_reason FROM decision_log WHERE client_id=%s",
            (client_id,),
        ).fetchall()


@requires_docker
@pytest.mark.usefixtures("app_dsn")
async def test_customer_turn_uses_jev_when_cascade_is_default_and_a_client_exists(
    migrated_dsn: str, app_dsn: str
) -> None:
    # The policy a new company gets: no decide_mode stored.
    client_id = _seed_customer_client(migrated_dsn, {"policy": {"deployment_mode": "shadow"}})
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev", confidence=0.9)
    pool = db.build_pool(app_dsn, max_size=2)
    await pool.open(wait=True)
    try:
        deps = PostgresTurnDependencies(
            pool,
            llm,  # type: ignore[arg-type]
            jev,  # type: ignore[arg-type]
            default_decide_mode="cascade",
        )
        envelope = make_envelope("i want 2 A1").model_copy(update={"client_id": client_id})
        signals = await deps.signals(envelope, Context())
    finally:
        await pool.close()
    assert signals.provider == "jev"
    assert llm.calls == 0
    assert _decision_rows(migrated_dsn, client_id) == [("cascade", "jev", None)]


@requires_docker
@pytest.mark.usefixtures("app_dsn")
async def test_force_decide_mode_sets_an_explicit_mode_that_wins(
    migrated_dsn: str, app_dsn: str
) -> None:
    client_id = _seed_customer_client(migrated_dsn, {"policy": {"deployment_mode": "auto"}})
    llm, jev = FakeClassifier("llm"), FakeClassifier("jev", confidence=0.9)
    pool = db.build_pool(app_dsn, max_size=2)
    await pool.open(wait=True)
    try:
        async with tx(pool, str(client_id)) as conn:
            forced = await force_decide_mode(conn, client_id, "llm")
        assert forced is not None
        assert forced["decide_mode"] == "llm"
        deps = PostgresTurnDependencies(
            pool,
            llm,  # type: ignore[arg-type]
            jev,  # type: ignore[arg-type]
            default_decide_mode="cascade",
        )
        envelope = make_envelope("i want 2 A1").model_copy(update={"client_id": client_id})
        signals = await deps.signals(envelope, Context())
    finally:
        await pool.close()
    assert signals.provider == "llm"
    assert jev.calls == 0
    assert _decision_rows(migrated_dsn, client_id) == []
