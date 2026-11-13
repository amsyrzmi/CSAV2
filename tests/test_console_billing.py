"""The client billing API (``/console/v1/billing``) on disposable synthetic tenants.

The clock is pinned through the ``billing_now`` dependency, so the open cycle is 5 Oct to 5 Nov
2026 whatever day the suite runs. Plans and ledger rows are written as the owner, the way the
service tests seed them; every figure is checked against ``cycle_usage`` itself.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal

import httpx
import pytest
from csa.billing.cycles import KL, Cycle
from csa.billing.memo import USAGE_MEMO
from csa.billing.service import CycleUsage, cycle_usage
from csa.console.billing import billing_now
from csa.core.tenancy import tx

from tests.conftest import requires_docker
from tests.test_console_sessions import MACHINE_TOKEN, SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/billing"
PATHS = (f"{BASE}/usage", f"{BASE}/usage/cycles")
NOW = datetime(2026, 10, 20, 12, 0, tzinfo=KL)
AUG = Cycle(date(2026, 8, 5), date(2026, 9, 5))
SEP = Cycle(date(2026, 9, 5), date(2026, 10, 5))
OCT = Cycle(date(2026, 10, 5), date(2026, 11, 5))
UNPRICED = "console-billing-no-price"  # never given a price row


@pytest.fixture(autouse=True)
def pinned_clock(sessions: SessionWorld) -> Iterator[None]:
    sessions.app.dependency_overrides[billing_now] = lambda: NOW
    USAGE_MEMO.clear()
    yield
    USAGE_MEMO.clear()


def kl(month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=KL)


async def plan(
    w: SessionWorld,
    tenant: str,
    effective: date = AUG.start,
    stage: str = "pilot",
    bundle: str = "individual",
) -> None:
    await w.q(
        "INSERT INTO client_billing_plans (client_id, stage, bundle, effective_from, reason,"
        " created_by) VALUES (%s, %s, %s, %s, 'synthetic', 'Synthetic Admin')",
        tenant,
        stage,
        bundle,
        effective,
    )


async def call(
    w: SessionWorld,
    tenant: str,
    at: datetime,
    *,
    job: str = "customer_turn",
    provider: str = "groq",
    model: str = "openai/gpt-oss-120b",
    prompt: int = 1_000_000,
    completion: int = 100_000,
    trace: uuid.UUID | None = None,
) -> uuid.UUID:
    """One ledger row; a new trace (one execution) unless ``trace`` is given."""
    trace = trace or uuid.uuid4()
    await w.q(
        "INSERT INTO ai_usage_events (client_id, occurred_at, trace_id, job, provider, model,"
        " prompt_tokens, completion_tokens, schema_name)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'Synthetic')",
        tenant,
        at,
        trace,
        job,
        provider,
        model,
        prompt,
        completion,
    )
    return trace


async def service_usage(w: SessionWorld, tenant: str, cycle: Cycle) -> CycleUsage:
    async with tx(w.pool, tenant) as conn:
        return await cycle_usage(conn, uuid.UUID(tenant), cycle, now=NOW)


async def admin_of(w: SessionWorld, tenant: str) -> httpx.AsyncClient:
    client, _ = await w.member(role="tenant_admin", tenant=tenant)
    return client


# --- access ----------------------------------------------------------------------------------


async def test_admin_sees_their_own_usage_matching_the_service(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, OCT.start, "collaborator", "curated")
    await call(w, tenant, kl(10, 6, 9), prompt=1_234_567, completion=7_654)
    await call(w, tenant, kl(10, 6, 10), job="email_reply", prompt=2_000_000)
    await call(w, tenant, kl(10, 8), job="hr_assistant", completion=900_000)
    admin = await admin_of(w, tenant)
    response = await admin.get(f"{BASE}/usage")
    assert response.status_code == 200, response.text
    body = response.json()
    expected = await service_usage(w, tenant, OCT)
    assert expected.bill is not None and expected.window is not None
    assert body["cycle_start"] == "2026-10-05" and body["cycle_end"] == "2026-11-05"
    assert (body["billed_from"], body["billed_to"]) == ("2026-10-05", "2026-11-05")
    assert body["executions"] == 3 and body["final"] is False and body["status"] == "ok"
    assert body["ai_usage_price"] == f"{expected.bill.ai_charge:.2f}"
    assert body["platform_fee"] == "119.99"
    assert body["total"] == f"{expected.bill.total:.2f}"
    assert Decimal(body["total"]) == Decimal(body["ai_usage_price"]) + Decimal("119.99")
    assert [(a["agent_id"], a["title"], a["executions"], a["price"]) for a in body["by_agent"]] == [
        (a.agent_id, a.title, a.executions, f"{a.price:.4f}") for a in expected.by_agent
    ]
    assert sum(Decimal(a["price"]) for a in body["by_agent"]) == expected.bill.ai_charge
    assert len(body["daily"]) == 31 and body["daily"][0] == {
        "day": "2026-10-05",
        "price": "0.0000",
    }
    assert sum(Decimal(d["price"]) for d in body["daily"]) == expected.bill.ai_charge


async def test_the_per_execution_list_does_not_exist(sessions: SessionWorld) -> None:
    """Owner decision 2026-11-13: the client sees the bill, per-agent and per-day charges and the
    execution count, never a list of single executions."""
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, OCT.start)
    await call(w, tenant, kl(10, 6))
    admin = await admin_of(w, tenant)
    for query in ("", "?cycle=2026-10-05", "?limit=2"):
        response = await admin.get(f"{BASE}/usage/executions{query}")
        assert response.status_code == 404, response.text
    platform = await w.admin.get(f"{BASE}/usage/executions?client_id={tenant}")
    assert platform.status_code == 404, platform.text


@pytest.mark.parametrize("role", ["tenant_staff", "read_only"])
async def test_a_member_who_is_not_an_admin_is_refused_everywhere(
    sessions: SessionWorld, role: str
) -> None:
    w = sessions
    await plan(w, w.tenants[0], OCT.start)
    await call(w, w.tenants[0], kl(10, 6))
    member, _ = await w.member(role=role)
    for path in PATHS:
        response = await member.get(path)
        assert response.status_code == 403, (path, response.text)
        assert "price" not in response.text


async def test_machine_principal_and_anonymous_are_refused(sessions: SessionWorld) -> None:
    w = sessions
    anonymous = await w.browser()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="https://console.example.test"
    ) as machine:
        for path in PATHS:
            assert (await anonymous.get(path)).status_code == 401
            refused = await machine.get(
                path,
                headers={"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": w.tenants[0]},
            )
            assert refused.status_code == 403, (path, refused.text)


async def test_tenants_never_see_each_other(sessions: SessionWorld) -> None:
    w = sessions
    mine, theirs = w.tenants
    await plan(w, mine, OCT.start)
    await plan(w, theirs, OCT.start)
    for day in (6, 7):
        await call(w, mine, kl(10, day))
    for day in (9, 10, 11):
        await call(w, theirs, kl(10, day, 15))
    for tenant, other, days in ((mine, theirs, [7, 6]), (theirs, mine, [11, 10, 9])):
        admin = await admin_of(w, tenant)
        usage = (await admin.get(f"{BASE}/usage")).json()
        assert usage["executions"] == len(days)
        hour = 12 if tenant == mine else 15
        assert [d["day"] for d in usage["daily"] if d["price"] != "0.0000"] == [
            kl(10, d, hour).date().isoformat() for d in sorted(days)
        ]
        for path in PATHS:
            assert (await admin.get(f"{path}?client_id={other}")).status_code == 403


async def test_platform_admin_selects_the_tenant(sessions: SessionWorld) -> None:
    w = sessions
    await plan(w, w.tenants[1], OCT.start)
    await call(w, w.tenants[1], kl(10, 6))
    for path in PATHS:
        assert (await w.admin.get(path)).status_code == 400  # client_id is required
    usage = await w.admin.get(f"{BASE}/usage?client_id={w.tenants[1]}")
    assert usage.status_code == 200, usage.text
    assert usage.json()["executions"] == 1
    cycles = await w.admin.get(f"{BASE}/usage/cycles?client_id={w.tenants[0]}")
    assert cycles.status_code == 200 and [c["start"] for c in cycles.json()] == ["2026-10-05"]
    unknown = await w.admin.get(f"{BASE}/usage?client_id={uuid.uuid4()}")
    assert unknown.status_code == 404


# --- the cycle parameter ---------------------------------------------------------------------


async def test_cycle_defaults_to_the_current_one_and_is_validated(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, AUG.start)
    await call(w, tenant, kl(9, 10))
    admin = await admin_of(w, tenant)
    current = (await admin.get(f"{BASE}/usage")).json()
    assert (current["cycle_start"], current["final"]) == ("2026-10-05", False)
    sep = (await admin.get(f"{BASE}/usage?cycle=2026-09-05")).json()
    assert (sep["cycle_start"], sep["cycle_end"], sep["final"]) == (
        "2026-09-05",
        "2026-10-05",
        True,
    )
    assert sep["executions"] == 1
    path = f"{BASE}/usage"
    assert (await admin.get(f"{path}?cycle=2026-09-06")).status_code == 422
    assert (await admin.get(f"{path}?cycle=not-a-date")).status_code == 422
    assert (await admin.get(f"{path}?cycle=2026-11-05")).status_code == 404
    assert (await admin.get(f"{path}?cycle=2027-01-05")).status_code == 404
    # The far future (past the last date a cycle can end) is the same 404, not a 500.
    far = await admin.get(f"{path}?cycle=9999-12-05")
    assert (far.status_code, far.json()["detail"]) == (404, "cycle_not_found")


# --- states ----------------------------------------------------------------------------------


async def test_not_set_up_shape(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    await call(w, tenant, kl(10, 6))  # usage, but no plan: nothing is billed
    admin = await admin_of(w, tenant)
    body = (await admin.get(f"{BASE}/usage")).json()
    assert body == {
        "cycle_start": "2026-10-05",
        "cycle_end": "2026-11-05",
        "billed_from": None,
        "billed_to": None,
        "executions": 0,
        "ai_usage_price": "0.00",
        "platform_fee": "0.00",
        "total": "0.00",
        "final": False,
        "status": "not_set_up",
        "by_agent": [],
        "daily": [],
    }
    cycles = (await admin.get(f"{BASE}/usage/cycles")).json()
    assert cycles == [{"start": "2026-10-05", "end": "2026-11-05", "total": None, "final": False}]


async def test_pending_prices_shape(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, OCT.start)
    await call(w, tenant, kl(10, 6))
    await call(w, tenant, kl(10, 7), job="hr_assistant", provider="synthetic", model=UNPRICED)
    admin = await admin_of(w, tenant)
    body = (await admin.get(f"{BASE}/usage")).json()
    assert body["status"] == "pending_prices" and body["executions"] == 2
    assert body["ai_usage_price"] == "1.50"
    assert body["by_agent"] == [
        {
            "agent_id": "support",
            "title": "Customer support assistant",
            "executions": 1,
            "price": "1.5000",
        },
        {"agent_id": "hr", "title": "HR assistant", "executions": 1, "price": "0.0000"},
    ]


async def test_cycles_newest_first_with_totals_and_final(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, date(2026, 8, 15))  # joined mid-cycle: the first period is prorated
    await call(w, tenant, kl(8, 20))
    await call(w, tenant, kl(9, 10))
    await call(w, tenant, kl(10, 10))
    admin = await admin_of(w, tenant)
    response = await admin.get(f"{BASE}/usage/cycles")
    assert response.status_code == 200, response.text
    picks = response.json()
    assert [(p["start"], p["end"], p["final"]) for p in picks] == [
        ("2026-10-05", "2026-11-05", False),
        ("2026-09-05", "2026-10-05", True),
        ("2026-08-05", "2026-09-05", True),
    ]
    for pick, cycle in zip(picks, (OCT, SEP, AUG), strict=True):
        expected = await service_usage(w, tenant, cycle)
        assert expected.bill is not None
        assert pick["total"] == f"{expected.bill.total:.2f}"
    # 99.99 x 21 / 31 = 67.74 plus RM1.50 of usage.
    assert picks[2]["total"] == "69.24"
    aug = (await admin.get(f"{BASE}/usage?cycle=2026-08-05")).json()
    assert (aug["billed_from"], aug["billed_to"]) == ("2026-08-15", "2026-09-05")
    # The closed cycles are memoised by the picker; the open one never is.
    assert len(USAGE_MEMO) == 2
