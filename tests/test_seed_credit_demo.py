"""The credit-monitoring demo seed (``scripts/seed_credit_demo.py``) on a disposable database.

The seed is run twice against the module's throwaway Postgres with the clock pinned to 20 Oct
2026, 12:00 Kuala Lumpur (open cycle 5 Oct to 5 Nov), the same clock the platform-admin price
route is given. The demo company starts with one unpriced execution in its billed window; adding
the missing price through ``POST /console/v1/billing/admin/prices`` (back-dated, which the shadow
rule allows only before the model's first row) turns its cycle from ``pending_prices`` into
``ok``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime

import pytest
from csa.billing.cycles import KL, Cycle
from csa.billing.memo import USAGE_MEMO
from csa.billing.service import CycleUsage, cycle_usage
from csa.console.billing import billing_now
from csa.core.tenancy import tx
from scripts.seed_credit_demo import (
    COLLABORATOR_CLIENT,
    DEMO_CLIENT,
    JOBS,
    UNPRICED_MODEL,
    seed_credit_demo,
)

from tests.conftest import requires_docker
from tests.test_console_sessions import SessionWorld

pytestmark = pytest.mark.asyncio
pytest_plugins = ["tests.test_console_sessions"]

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=KL)
SEP = Cycle(date(2026, 9, 5), date(2026, 10, 5))
OCT = Cycle(date(2026, 10, 5), date(2026, 11, 5))
PRICES = "/console/v1/billing/admin/prices"


@pytest.fixture
def pinned_clock(sessions: SessionWorld) -> Iterator[None]:
    sessions.app.dependency_overrides[billing_now] = lambda: NOW
    USAGE_MEMO.clear()
    yield
    USAGE_MEMO.clear()


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://csa:pw@db.example.test:5444/csa",
        "postgresql://csa:pw@10.0.0.5:5444/csa",
        "postgresql://csa:pw@127.0.0.1:5432/csa",
        "postgresql://csa_app:pw@127.0.0.1:5444/csa",
    ],
)
async def test_refuses_a_dsn_that_is_not_the_isolated_local_dev_database(
    dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENV", "ci")
    with pytest.raises(ValueError, match="loopback"):
        await seed_credit_demo(dsn, now=NOW)


async def test_refuses_without_an_explicit_dev_or_ci_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENV", "prod")
    with pytest.raises(ValueError, match="ENV=dev or ENV=ci"):
        await seed_credit_demo("postgresql://csa:pw@127.0.0.1:5444/csa", now=NOW)


async def _counts(w: SessionWorld) -> dict[str, int]:
    rows = await w.q(
        "SELECT 'usage:' || client_id AS k, count(*) AS n FROM ai_usage_events GROUP BY client_id"
        " UNION ALL SELECT 'plans:' || client_id, count(*) FROM client_billing_plans"
        " GROUP BY client_id"
        " UNION ALL SELECT 'audit', count(*) FROM console_audit"
        " WHERE action = 'billing.plan_inserted'"
        " UNION ALL SELECT 'prices', count(*) FROM ai_model_prices"
    )
    return {r["k"]: int(r["n"]) for r in rows}


async def _usage(w: SessionWorld, client_id: uuid.UUID, cycle: Cycle) -> CycleUsage:
    async with tx(w.pool, str(client_id)) as conn:
        return await cycle_usage(conn, client_id, cycle, now=NOW)


@requires_docker
async def test_seed_is_idempotent_and_prices_once_the_missing_price_is_back_dated(
    sessions: SessionWorld, pinned_clock: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENV", "ci")
    first = await seed_credit_demo(sessions.owner_dsn, now=NOW)
    after_first = await _counts(sessions)
    second = await seed_credit_demo(sessions.owner_dsn, now=NOW)
    assert await _counts(sessions) == after_first
    assert first.usage_rows_added > 0 and first.plans_added == 2
    assert second.usage_rows_added == 0 and second.plans_added == 0

    # The shape the manual test relies on.
    demo = f"usage:{DEMO_CLIENT}"
    assert 200 <= after_first[demo] <= 1000
    assert after_first[f"plans:{DEMO_CLIENT}"] == 1
    assert after_first[f"plans:{COLLABORATOR_CLIENT}"] == 1
    assert after_first["audit"] == 2
    plans = await sessions.q(
        "SELECT client_id, stage, bundle, effective_from FROM client_billing_plans"
    )
    by_client = {str(p["client_id"]): p for p in plans}
    assert (by_client[str(DEMO_CLIENT)]["stage"], by_client[str(DEMO_CLIENT)]["bundle"]) == (
        "pilot",
        "individual",
    )
    assert OCT.start < by_client[str(DEMO_CLIENT)]["effective_from"] <= NOW.date()
    collaborator = by_client[str(COLLABORATOR_CLIENT)]
    assert (collaborator["stage"], collaborator["bundle"]) == ("collaborator", "complete")
    assert collaborator["effective_from"] == OCT.start

    rows = await sessions.q(
        "SELECT trace_id, occurred_at, job, provider, model FROM ai_usage_events"
        " WHERE client_id = %s ORDER BY occurred_at",
        DEMO_CLIENT,
    )
    assert {r["job"] for r in rows} == set(JOBS)
    assert {r["provider"] for r in rows} == {"groq", "gemini"}
    assert all(r["occurred_at"] <= NOW for r in rows)
    assert min(r["occurred_at"] for r in rows) >= datetime(2026, 9, 5, tzinfo=KL)
    traces: dict[uuid.UUID, list[datetime]] = {}
    for r in rows:
        traces.setdefault(r["trace_id"], []).append(r["occurred_at"])
    assert max(len(calls) for calls in traces.values()) >= 3  # a multi-call execution
    boundary = datetime(2026, 10, 5, tzinfo=KL)
    assert any(min(c) < boundary <= max(c) for c in traces.values())  # straddles the cycle start
    assert sum(1 for r in rows if r["model"] == UNPRICED_MODEL) >= 1

    before = await _usage(sessions, DEMO_CLIENT, OCT)
    assert before.status == "pending_prices"
    assert before.unpriced_executions == 1
    assert before.usage_without_plan > 0  # calls between the cycle start and the join date
    assert before.window is not None and before.window[0] > OCT.start  # prorated first period
    assert {a.agent_id for a in before.by_agent} == {
        "support",
        "assistant",
        "hr",
        "email",
        "training",
    }
    previous = await _usage(sessions, DEMO_CLIENT, SEP)
    assert previous.status == "not_set_up" and previous.usage_without_plan > 0
    collab = await _usage(sessions, COLLABORATOR_CLIENT, OCT)
    assert collab.status == "ok" and collab.executions == []  # the zero-usage state
    assert collab.bill is not None and str(collab.bill.total) == "119.99"

    # The platform admin back-dates the missing price; the shadow rule allows it only because no
    # row exists yet for that model, and refuses a second back-dated row inside the priced range.
    body = {
        "provider": "groq",
        "model": UNPRICED_MODEL,
        "input_usd_per_mtok": "0.10",
        "output_usd_per_mtok": "0.50",
        "effective_from": "2026-09-05T00:00:00+08:00",
    }
    added = await sessions.mutate(sessions.admin, PRICES, body)
    assert added.status_code == 201, added.text
    shadowed = await sessions.mutate(
        sessions.admin, PRICES, {**body, "effective_from": "2026-10-10T00:00:00+08:00"}
    )
    assert shadowed.status_code == 422 and shadowed.json()["detail"] == "shadow_rule"

    after = await _usage(sessions, DEMO_CLIENT, OCT)
    assert after.status == "ok"
    assert after.unpriced_executions == 0
    assert len(after.executions) == len(before.executions)
    assert after.bill is not None and before.bill is not None
    assert after.bill.ai_charge > before.bill.ai_charge

    # A third run still adds nothing, and leaves the admin's price alone.
    counts = await _counts(sessions)
    third = await seed_credit_demo(sessions.owner_dsn, now=NOW)
    assert third.usage_rows_added == 0 and third.plans_added == 0
    assert await _counts(sessions) == counts
