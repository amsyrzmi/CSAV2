from __future__ import annotations

import uuid

import psycopg
import pytest
from csa.contracts.envelope import Envelope
from csa.contracts.plan import Intent
from csa.contracts.state import Context, Signals
from csa.core import db
from csa.decide.jev import JevError
from csa.decide.providers import classify_with
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
