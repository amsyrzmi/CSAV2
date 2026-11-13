"""The platform-admin billing API (``/console/v1/billing/admin``) on disposable synthetic tenants.

The clock is pinned through ``billing_now`` (20 Oct 2026, 12:00 Kuala Lumpur), so the open cycle
is 5 Oct to 5 Nov 2026. Price and FX rows are global and the database lives for the module, so
every test prices its own synthetic model, and FX rows repeat the seeded rate (4.0840) so no other
test's figures move. Every figure is checked against ``cycle_usage`` itself.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
import pytest
from csa.billing.cycles import Cycle
from csa.billing.memo import USAGE_MEMO, fingerprint
from csa.billing.service import CycleUsage, cycle_usage
from csa.console.billing import billing_now
from csa.console.billing_admin import (
    AdminCompanyOut,
    AdminOverviewOut,
    BillingFxIn,
    BillingFxOut,
    BillingPlanIn,
    BillingPlanOut,
    BillingPriceIn,
    BillingPriceOut,
    LangfuseCheckOut,
)
from csa.core.tenancy import tx
from csa.metering.meter import LEDGER_PROVIDERS
from pydantic import BaseModel
from scripts.export_console_openapi import contract

from tests.conftest import requires_docker
from tests.test_console_billing import AUG, BASE, NOW, OCT, SEP, UNPRICED, call, kl, plan
from tests.test_console_billing_contract import CLIENT_MODELS, FORBIDDEN
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld

pytest_plugins = ["tests.test_console_sessions"]

ADMIN = f"{BASE}/admin"
OVERVIEW = f"{ADMIN}/overview"
PRICES = f"{ADMIN}/prices"
FX = f"{ADMIN}/fx"
SEED_FX = "4.0840"  # the seeded rate: FX rows written here never move another test's figures
ADMIN_MODELS: tuple[type[BaseModel], ...] = (
    AdminOverviewOut,
    AdminCompanyOut,
    BillingPlanIn,
    BillingPlanOut,
    BillingPriceIn,
    BillingPriceOut,
    BillingFxIn,
    BillingFxOut,
    LangfuseCheckOut,
)


@pytest.fixture
def pinned_clock(sessions: SessionWorld) -> Iterator[None]:
    sessions.app.dependency_overrides[billing_now] = lambda: NOW
    USAGE_MEMO.clear()
    yield
    USAGE_MEMO.clear()


def plan_path(client_id: str) -> str:
    return f"{ADMIN}/clients/{client_id}/plan"


def check_path(client_id: str) -> str:
    return f"{ADMIN}/clients/{client_id}/langfuse-check"


async def company(w: SessionWorld, name: str) -> str:
    created = await w.mutate(
        w.admin,
        "/console/v1/admin/clients",
        {"slug": f"synthetic-{uuid.uuid4()}", "name": name},
    )
    assert created.status_code == 201, created.text
    return str(created.json()["id"])


async def overview(w: SessionWorld, cycle: str | None = None) -> dict[str, Any]:
    response = await w.admin.get(OVERVIEW if cycle is None else f"{OVERVIEW}?cycle={cycle}")
    assert response.status_code == 200, response.text
    return response.json()


def row_of(body: dict[str, Any], client_id: str) -> dict[str, Any]:
    [row] = [c for c in body["companies"] if c["client_id"] == client_id]
    return row


async def service_usage(w: SessionWorld, tenant: str, cycle: Cycle) -> CycleUsage:
    async with tx(w.pool, tenant) as conn:
        return await cycle_usage(conn, uuid.UUID(tenant), cycle, now=NOW)


def model_name() -> str:
    return f"synthetic-model-{uuid.uuid4()}"


def price_body(model: str, at: str, *, provider: str = "groq", **extra: Any) -> dict:
    return {
        "provider": provider,
        "model": model,
        "input_usd_per_mtok": "0.15",
        "output_usd_per_mtok": "0.60",
        "effective_from": at,
        **extra,
    }


def round_to(value: Decimal, places: str) -> str:
    return str(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


# --- access ----------------------------------------------------------------------------------


async def requests_for(w: SessionWorld, client: httpx.AsyncClient, tenant: str) -> list:
    """One request to each of the five routes, with a valid CSRF token for the writes."""
    model = model_name()
    return [
        await client.get(f"{OVERVIEW}?client_id={tenant}"),
        await client.get(check_path(tenant)),
        await w.mutate(
            client,
            plan_path(tenant),
            {
                "stage": "pilot",
                "bundle": "individual",
                "effective_from": "2026-10-05",
                "reason": "synthetic",
            },
        ),
        await w.mutate(client, PRICES, price_body(model, "2026-10-20T00:00:00+08:00")),
        await w.mutate(
            client, FX, {"usd_to_myr": SEED_FX, "effective_from": "2026-10-21T00:00:00+08:00"}
        ),
    ]


@requires_docker
@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
async def test_members_are_refused_on_every_admin_route(
    sessions: SessionWorld, pinned_clock: None, role: str
) -> None:
    w = sessions
    tenant = w.tenants[0]
    member, _ = await w.member(role=role, tenant=tenant)
    fx_before = await w.q("SELECT count(*) AS n FROM billing_fx_rates")
    for response in await requests_for(w, member, tenant):
        assert response.status_code == 403, (response.request.url, response.text)
        assert "raw_cost" not in response.text
        if role == "tenant_admin":  # even for their own company
            assert response.json()["detail"] == "platform_admin_only"
    assert await w.q("SELECT 1 FROM client_billing_plans WHERE client_id = %s", tenant) == []
    assert await w.q("SELECT count(*) AS n FROM billing_fx_rates") == fx_before


@requires_docker
@pytest.mark.asyncio
async def test_machine_principal_and_anonymous_are_refused(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = w.tenants[0]
    anonymous = await w.browser()
    for response in await requests_for(w, anonymous, tenant):
        assert response.status_code == 401, (response.request.url, response.text)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url=ORIGIN
    ) as machine:
        headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": tenant}
        refused = [
            await machine.get(OVERVIEW, headers=headers),
            await machine.get(check_path(tenant), headers=headers),
            await machine.post(plan_path(tenant), json={}, headers=headers),
            await machine.post(PRICES, json={}, headers=headers),
            await machine.post(FX, json={}, headers=headers),
        ]
    for response in refused:
        assert response.status_code == 403, (response.request.url, response.text)


@requires_docker
@pytest.mark.asyncio
async def test_writes_need_the_csrf_token_like_every_console_write(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = w.tenants[0]
    model = model_name()
    no_token = {"Origin": ORIGIN}
    reference = await w.admin.post(
        "/console/v1/admin/clients",
        json={"slug": f"synthetic-{uuid.uuid4()}", "name": "Never Created"},
        headers=no_token,
    )
    assert reference.status_code == 403
    refused = [
        await w.admin.post(
            plan_path(tenant),
            json={
                "stage": "pilot",
                "bundle": "individual",
                "effective_from": "2026-10-05",
                "reason": "synthetic",
            },
            headers=no_token,
        ),
        await w.admin.post(
            PRICES, json=price_body(model, "2026-10-20T00:00:00+08:00"), headers=no_token
        ),
        await w.admin.post(
            FX,
            json={"usd_to_myr": SEED_FX, "effective_from": "2026-10-21T00:00:00+08:00"},
            headers=no_token,
        ),
    ]
    for response in refused:
        assert (response.status_code, response.json()) == (403, reference.json())
    assert await w.q("SELECT 1 FROM client_billing_plans WHERE client_id = %s", tenant) == []
    assert await w.q("SELECT 1 FROM ai_model_prices WHERE model = %s", model) == []


# --- overview --------------------------------------------------------------------------------


@requires_docker
@pytest.mark.asyncio
async def test_overview_carries_tokens_raw_cost_and_margin(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, OCT.start, "collaborator", "curated")
    await call(w, tenant, kl(10, 6, 9), prompt=1_234_567, completion=7_654)
    await call(w, tenant, kl(10, 6, 10), job="email_reply", prompt=2_000_000)
    await call(w, tenant, kl(10, 8), job="hr_assistant", completion=900_000)
    body = await overview(w)
    assert (body["cycle_start"], body["cycle_end"], body["final"]) == (
        "2026-10-05",
        "2026-11-05",
        False,
    )
    row = row_of(body, tenant)
    expected = await service_usage(w, tenant, OCT)
    assert expected.bill is not None and expected.raw_cost_rm > 0
    margin = expected.bill.ai_charge - expected.raw_cost_rm
    assert row == {
        "client_id": tenant,
        "name": "Synthetic Session Tenant",
        "status": "ok",
        "stage": "collaborator",
        "bundle": "curated",
        "executions": 3,
        "prompt_tokens": expected.prompt_tokens,
        "completion_tokens": expected.completion_tokens,
        "raw_cost_rm": round_to(expected.raw_cost_rm, "0.0001"),
        "ai_charge": f"{expected.bill.ai_charge:.2f}",
        "platform_fee": "119.99",
        "billed_total": f"{expected.bill.total:.2f}",
        "ai_margin_rm": round_to(margin, "0.01"),
        "cost_per_execution_rm": round_to(expected.raw_cost_rm / 3, "0.0001"),
        "workbook_cost_per_message_rm": "0.0167",
        "unpriced_calls": 0,
        "unpriced_executions": 0,
        "usage_without_plan": 0,
        "usage_after_termination": 0,
        "terminated_on": None,
        "warnings": [],
    }
    assert row["prompt_tokens"] == 1_234_567 + 2_000_000 + 1_000_000
    assert abs(
        Decimal(row["ai_margin_rm"]) - (Decimal(row["ai_charge"]) - expected.raw_cost_rm)
    ) <= Decimal("0.005")


@requires_docker
@pytest.mark.asyncio
async def test_overview_lists_every_company_by_name_with_each_warning(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    quiet = await company(w, "Aa Quiet Sdn Bhd")  # no plan and no usage
    unplanned = await company(w, "Ab Unplanned Sdn Bhd")
    await call(w, unplanned, kl(10, 6))
    ended = await company(w, "Ac Long Pilot Sdn Bhd")
    await plan(w, ended, date(2026, 6, 5))  # three months on: the pilot ended 5 Sep
    left = await company(w, "Ad Left Sdn Bhd")
    await plan(w, left, AUG.start, "collaborator", "complete")
    await plan(w, left, date(2026, 10, 10), "terminated", "complete")
    await call(w, left, kl(10, 8))
    await call(w, left, kl(10, 12))  # after the termination: not billed
    pending = await company(w, "Ae Pending Sdn Bhd")
    await plan(w, pending, OCT.start)
    await call(w, pending, kl(10, 6))
    await call(w, pending, kl(10, 7), provider="synthetic", model=UNPRICED)
    joined = await company(w, "Af Joined Late Sdn Bhd")
    await plan(w, joined, date(2026, 10, 10))
    await call(w, joined, kl(10, 6))  # before the join date: not billed
    await call(w, joined, kl(10, 7))
    await call(w, joined, kl(10, 12))

    body = await overview(w)
    names = [c["name"] for c in body["companies"]]
    assert names == sorted(names)
    assert row_of(body, quiet) | {"client_id": None} == {
        "client_id": None,
        "name": "Aa Quiet Sdn Bhd",
        "status": "not_set_up",
        "stage": None,
        "bundle": None,
        "executions": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "raw_cost_rm": "0.0000",
        "ai_charge": "0.00",
        "platform_fee": "0.00",
        "billed_total": "0.00",
        "ai_margin_rm": "0.00",
        "cost_per_execution_rm": None,
        "workbook_cost_per_message_rm": "0.0167",
        "unpriced_calls": 0,
        "unpriced_executions": 0,
        "usage_without_plan": 0,
        "usage_after_termination": 0,
        "terminated_on": None,
        "warnings": [],
    }
    no_plan = row_of(body, unplanned)
    assert (no_plan["status"], no_plan["warnings"]) == ("not_set_up", ["not_set_up_with_usage"])
    assert (no_plan["usage_without_plan"], no_plan["terminated_on"]) == (1, None)
    pilot = row_of(body, ended)
    assert (pilot["status"], pilot["stage"], pilot["warnings"]) == ("ok", "pilot", ["pilot_ended"])
    gone = row_of(body, left)
    assert gone["warnings"] == ["usage_after_termination"]
    assert (gone["terminated_on"], gone["usage_after_termination"]) == ("2026-10-10", 1)
    assert gone["usage_without_plan"] == 0
    assert (gone["status"], gone["stage"], gone["executions"]) == ("ok", "collaborator", 1)
    late = row_of(body, pending)
    assert (late["status"], late["warnings"]) == ("pending_prices", ["pending_prices"])
    assert (late["executions"], late["unpriced_calls"], late["unpriced_executions"]) == (2, 1, 1)
    expected = await service_usage(w, pending, OCT)
    # The real cost per execution is over the priced executions only: the unpriced one has none.
    assert late["cost_per_execution_rm"] == round_to(expected.raw_cost_rm, "0.0001")
    early = row_of(body, joined)
    assert (early["status"], early["warnings"]) == ("ok", ["usage_before_plan"])
    assert (early["usage_without_plan"], early["executions"]) == (2, 1)


@requires_docker
@pytest.mark.asyncio
async def test_overview_cycle_parameter_follows_the_client_rules(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = w.tenants[0]
    await plan(w, tenant, AUG.start)
    await call(w, tenant, kl(9, 10))
    sep = await overview(w, "2026-09-05")
    assert (sep["cycle_start"], sep["cycle_end"], sep["final"]) == (
        "2026-09-05",
        "2026-10-05",
        True,
    )
    assert row_of(sep, tenant)["executions"] == 1
    cases = (("2026-09-06", 422), ("nope", 422), ("2026-11-05", 404), ("9999-12-05", 404))
    for cycle, status in cases:
        response = await w.admin.get(f"{OVERVIEW}?cycle={cycle}")
        assert response.status_code == status, (cycle, response.text)
    far = await w.admin.get(f"{OVERVIEW}?cycle=9999-12-05")
    assert far.json()["detail"] == "cycle_not_found"


# --- plan ------------------------------------------------------------------------------------


async def post_plan(
    w: SessionWorld,
    tenant: str,
    stage: str,
    effective: str,
    bundle: str | None = "individual",
    reason: str = "synthetic change",
) -> httpx.Response:
    body: dict[str, Any] = {"stage": stage, "effective_from": effective, "reason": reason}
    if bundle is not None:
        body["bundle"] = bundle
    return await w.mutate(w.admin, plan_path(tenant), body)


@requires_docker
@pytest.mark.asyncio
async def test_a_valid_first_plan_row_is_stored_and_audited(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = await company(w, "First Plan Sdn Bhd")
    response = await post_plan(w, tenant, "pilot", "2026-10-12", "curated", "joined")
    assert response.status_code == 201, response.text
    created = response.json()
    assert created == {
        "client_id": tenant,
        "stage": "pilot",
        "bundle": "curated",
        "effective_from": "2026-10-12",
        "created_at": created["created_at"],
    }
    [stored] = await w.q(
        "SELECT stage, bundle, effective_from, reason, created_by FROM client_billing_plans"
        " WHERE client_id = %s",
        tenant,
    )
    assert stored == {
        "stage": "pilot",
        "bundle": "curated",
        "effective_from": date(2026, 10, 12),
        "reason": "joined",
        "created_by": "Synthetic Admin",
    }
    [audit] = await w.q(
        "SELECT actor_id::text AS actor_id, action, details FROM console_audit"
        " WHERE client_id = %s AND action = 'billing.plan_inserted'",
        tenant,
    )
    assert audit["actor_id"] == w.admin_id
    assert audit["details"]["before"] is None
    assert audit["details"]["after"] == {
        "stage": "pilot",
        "bundle": "curated",
        "effective_from": "2026-10-12",
    }
    # A second row records the plan it replaces as "before".
    assert (await post_plan(w, tenant, "collaborator", "2026-11-05", "curated")).status_code == 201
    rows = await w.q(
        "SELECT details FROM console_audit WHERE client_id = %s"
        " AND action = 'billing.plan_inserted' ORDER BY created_at",
        tenant,
    )
    assert rows[1]["details"]["before"] == audit["details"]["after"]
    assert rows[1]["details"]["after"]["stage"] == "collaborator"
    row = row_of(await overview(w), tenant)
    assert (row["stage"], row["bundle"], row["platform_fee"]) == ("pilot", "curated", "77.41")


@requires_docker
@pytest.mark.asyncio
async def test_every_plan_rule_maps_to_422_with_its_code(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    fresh = await company(w, "Rules Fresh Sdn Bhd")
    cases: list[tuple[str, str, str, str | None, str]] = [
        (
            fresh,
            "terminated",
            "2026-10-12",
            "individual",
            "first_row_must_be_pilot_or_collaborator",
        ),
        (fresh, "terminated", "2026-10-12", None, "first_row_must_be_pilot_or_collaborator"),
        (fresh, "pilot", "2026-09-05", "individual", "first_row_outside_current_cycle"),
    ]
    old = await company(w, "Rules Old Sdn Bhd")
    await plan(w, old, AUG.start)
    cases.append((old, "collaborator", "2026-09-05", "individual", "before_current_cycle"))
    late = await company(w, "Rules Late Sdn Bhd")
    assert (await post_plan(w, late, "pilot", "2026-10-10")).status_code == 201
    cases.append((late, "collaborator", "2026-10-05", "individual", "before_join_date"))
    fifth = await company(w, "Rules Fifth Sdn Bhd")
    assert (await post_plan(w, fifth, "pilot", "2026-10-05")).status_code == 201
    cases.append((fifth, "collaborator", "2026-10-12", "individual", "change_not_on_a_fifth"))
    back = await company(w, "Rules Back Sdn Bhd")
    assert (await post_plan(w, back, "collaborator", "2026-10-05")).status_code == 201
    cases.append((back, "pilot", "2026-11-05", "individual", "bundle_change_same_stage_only"))
    ended = await company(w, "Rules Ended Sdn Bhd")
    assert (await post_plan(w, ended, "pilot", "2026-10-05")).status_code == 201
    assert (await post_plan(w, ended, "terminated", "2026-10-25")).status_code == 201
    cases.append((ended, "collaborator", "2026-11-05", "individual", "after_termination"))

    for tenant, stage, effective, bundle, code in cases:
        before = await w.q(
            "SELECT count(*) AS n FROM client_billing_plans WHERE client_id = %s", tenant
        )
        response = await post_plan(w, tenant, stage, effective, bundle)
        assert (response.status_code, response.json()) == (422, {"detail": code}), (
            code,
            response.text,
        )
        after = await w.q(
            "SELECT count(*) AS n FROM client_billing_plans WHERE client_id = %s", tenant
        )
        assert before == after


@requires_docker
@pytest.mark.asyncio
async def test_plan_request_validation_and_unknown_company(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = await company(w, "Validation Sdn Bhd")
    missing_bundle = await post_plan(w, tenant, "pilot", "2026-10-05", None)
    assert missing_bundle.status_code == 422
    for bad in (
        {"stage": "gold", "bundle": "individual", "effective_from": "2026-10-05", "reason": "x"},
        {"stage": "pilot", "bundle": "huge", "effective_from": "2026-10-05", "reason": "x"},
        {"stage": "pilot", "bundle": "individual", "effective_from": "2026-10-05", "reason": " "},
        {"stage": "pilot", "bundle": "individual", "effective_from": "2026-10-05"},
        {
            "stage": "pilot",
            "bundle": "individual",
            "effective_from": "2026-10-05",
            "reason": "x",
            "created_by": "someone else",
        },
    ):
        assert (await w.mutate(w.admin, plan_path(tenant), bad)).status_code == 422, bad
    unknown = await post_plan(w, str(uuid.uuid4()), "pilot", "2026-10-05")
    assert unknown.status_code == 404
    assert await w.q("SELECT 1 FROM client_billing_plans WHERE client_id = %s", tenant) == []


@requires_docker
@pytest.mark.asyncio
async def test_a_termination_takes_the_prior_bundle_and_may_precede_a_scheduled_row(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = await company(w, "Termination Sdn Bhd")
    assert (await post_plan(w, tenant, "pilot", "2026-10-05", "complete")).status_code == 201
    scheduled = await post_plan(w, tenant, "collaborator", "2026-11-05", "curated")
    assert scheduled.status_code == 201
    ended = await post_plan(w, tenant, "terminated", "2026-10-25", None)
    assert ended.status_code == 201, ended.text
    # The plan in force on 25 Oct is pilot/complete, not the later scheduled collaborator row,
    # so the stored bundle matches the audit's "before".
    assert (ended.json()["stage"], ended.json()["bundle"]) == ("terminated", "complete")
    [audit] = await w.q(
        "SELECT details FROM console_audit WHERE client_id = %s"
        " AND action = 'billing.plan_inserted' AND details->'after'->>'stage' = 'terminated'",
        tenant,
    )
    assert audit["details"]["before"] == {
        "stage": "pilot",
        "bundle": "complete",
        "effective_from": "2026-10-05",
    }
    row = row_of(await overview(w), tenant)
    assert (row["terminated_on"], row["stage"], row["bundle"]) == (
        "2026-10-25",
        "pilot",
        "complete",
    )


@requires_docker
@pytest.mark.asyncio
async def test_a_termination_before_the_join_date_is_refused_by_the_rules(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = await company(w, "Early Termination Sdn Bhd")
    assert (await post_plan(w, tenant, "pilot", "2026-10-12", "curated")).status_code == 201
    # No row is in force on 6 Oct: the bundle falls back to the latest row, and the rules refuse.
    early = await post_plan(w, tenant, "terminated", "2026-10-06", None)
    assert (early.status_code, early.json()) == (422, {"detail": "before_join_date"})


# --- prices ----------------------------------------------------------------------------------


@requires_docker
@pytest.mark.asyncio
async def test_a_back_dated_price_prices_a_pending_closed_cycle_end_to_end(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = await company(w, "Unpriced Model Sdn Bhd")
    model = model_name()
    await plan(w, tenant, AUG.start)
    await call(w, tenant, kl(9, 10), model=model)  # groq, on a model with no price yet
    await call(w, tenant, kl(9, 11))
    pending = row_of(await overview(w, "2026-09-05"), tenant)
    assert (pending["status"], pending["warnings"]) == ("pending_prices", ["pending_prices"])
    assert (pending["unpriced_calls"], pending["unpriced_executions"]) == (1, 1)
    assert len(USAGE_MEMO) > 0  # the closed cycle is memoised from here on

    today = await w.mutate(w.admin, PRICES, price_body(model, "2026-10-20T00:00:00+08:00"))
    assert today.status_code == 201, today.text
    assert row_of(await overview(w, "2026-09-05"), tenant)["status"] == "pending_prices"

    back = await w.mutate(w.admin, PRICES, price_body(model, "2026-09-01T00:00:00+08:00"))
    assert back.status_code == 201, back.text
    assert back.json() == {
        "provider": "groq",
        "model": model,
        "input_usd_per_mtok": "0.15",
        "output_usd_per_mtok": "0.60",
        "effective_from": back.json()["effective_from"],
        "created_at": back.json()["created_at"],
    }
    # 10 Oct now lies inside the range the 1 Sep row prices: refused.
    inside = await w.mutate(w.admin, PRICES, price_body(model, "2026-10-10T00:00:00+08:00"))
    assert (inside.status_code, inside.json()) == (422, {"detail": "shadow_rule"})
    priced = row_of(await overview(w, "2026-09-05"), tenant)
    expected = await service_usage(w, tenant, SEP)
    assert expected.status == "ok" and expected.bill is not None
    assert (priced["status"], priced["warnings"], priced["unpriced_calls"]) == ("ok", [], 0)
    assert priced["raw_cost_rm"] == round_to(expected.raw_cost_rm, "0.0001")
    assert priced["ai_margin_rm"] == round_to(
        expected.bill.ai_charge - expected.raw_cost_rm, "0.01"
    )
    assert Decimal(priced["raw_cost_rm"]) > Decimal(pending["raw_cost_rm"])


@requires_docker
@pytest.mark.asyncio
async def test_price_shadow_rule_duplicates_and_invalid_numbers(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    model = model_name()
    tomorrow = "2026-10-21T00:00:00+08:00"
    assert (await w.mutate(w.admin, PRICES, price_body(model, tomorrow))).status_code == 201
    duplicate = await w.mutate(w.admin, PRICES, price_body(model, tomorrow))
    assert duplicate.status_code == 409
    too_far = await w.mutate(w.admin, PRICES, price_body(model, "2026-10-23T00:00:00+08:00"))
    assert (too_far.status_code, too_far.json()) == (422, {"detail": "shadow_rule"})
    for field in ("input_usd_per_mtok", "output_usd_per_mtok"):
        for bad in ("0", "-1", "NaN", "Infinity", "100000", "1e400", "abc", "", None, True):
            body = price_body(model_name(), tomorrow) | {field: bad}
            response = await w.mutate(w.admin, PRICES, body)
            assert response.status_code == 422, (field, bad, response.text)
    for bad_body in (
        price_body(model_name(), "2026-10-21T00:00:00"),  # naive
        price_body(model_name(), tomorrow, provider=""),
        price_body(" padded ", tomorrow),
        price_body(model_name(), tomorrow, provider=" groq"),
        price_body(model_name(), tomorrow, extra="field"),
    ):
        assert (await w.mutate(w.admin, PRICES, bad_body)).status_code == 422, bad_body
    rows = await w.q("SELECT count(*) AS n FROM ai_model_prices WHERE model = %s", model)
    assert rows == [{"n": 1}]


@requires_docker
@pytest.mark.asyncio
async def test_only_a_provider_the_ledger_records_may_be_priced(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    assert LEDGER_PROVIDERS == {"groq", "gemini"}
    for provider in sorted(LEDGER_PROVIDERS):
        body = price_body(model_name(), "2026-10-21T00:00:00+08:00", provider=provider)
        response = await w.mutate(w.admin, PRICES, body)
        assert response.status_code == 201, (provider, response.text)
    model = model_name()
    for provider in ("synthetic", "Groq", "GEMINI", "openai"):
        body = price_body(model, "2026-10-21T00:00:00+08:00", provider=provider)
        response = await w.mutate(w.admin, PRICES, body)
        assert (response.status_code, response.json()) == (422, {"detail": "unknown_provider"})
    assert await w.q("SELECT 1 FROM ai_model_prices WHERE model = %s", model) == []


# --- FX --------------------------------------------------------------------------------------


@requires_docker
@pytest.mark.asyncio
async def test_fx_back_dating_shadow_rule_duplicates_and_invalid_numbers(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    [earliest] = await w.q("SELECT min(effective_from) AS at FROM billing_fx_rates")
    before = earliest["at"].replace(year=earliest["at"].year - 1).isoformat()
    back = await w.mutate(w.admin, FX, {"usd_to_myr": SEED_FX, "effective_from": before})
    assert back.status_code == 201, back.text
    assert back.json()["usd_to_myr"] == SEED_FX
    inside = await w.mutate(
        w.admin, FX, {"usd_to_myr": SEED_FX, "effective_from": "2026-10-01T00:00:00+08:00"}
    )
    assert (inside.status_code, inside.json()) == (422, {"detail": "shadow_rule"})
    today = {"usd_to_myr": SEED_FX, "effective_from": "2026-10-20T00:00:00+08:00"}
    assert (await w.mutate(w.admin, FX, today)).status_code == 201
    assert (await w.mutate(w.admin, FX, today)).status_code == 409
    fx_before = await w.q("SELECT count(*) AS n FROM billing_fx_rates")
    for bad in ("0", "-4", "NaN", "-Infinity", "1000", "x", None):
        body = {"usd_to_myr": bad, "effective_from": "2026-10-21T00:00:00+08:00"}
        assert (await w.mutate(w.admin, FX, body)).status_code == 422, bad
    assert await w.q("SELECT count(*) AS n FROM billing_fx_rates") == fx_before


# --- audit and the memo ----------------------------------------------------------------------


@requires_docker
@pytest.mark.asyncio
async def test_price_and_fx_writes_are_audited_with_before_and_after(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    model = model_name()
    first = await w.mutate(w.admin, PRICES, price_body(model, "2026-10-20T00:00:00+08:00"))
    assert first.status_code == 201
    second = await w.mutate(
        w.admin,
        PRICES,
        price_body(model, "2026-10-21T00:00:00+08:00", input_usd_per_mtok="0.2"),
    )
    assert second.status_code == 201
    audits = await w.q(
        "SELECT actor_id::text AS actor_id, client_id, target_id, details FROM console_audit"
        " WHERE action = 'billing.price_added' AND details->'after'->>'model' = %s"
        " ORDER BY created_at",
        model,
    )
    assert [a["actor_id"] for a in audits] == [w.admin_id, w.admin_id]
    assert [a["client_id"] for a in audits] == [None, None]  # prices are global
    stored = await w.q(
        "SELECT id::text AS id FROM ai_model_prices WHERE model = %s ORDER BY effective_from",
        model,
    )
    assert [a["target_id"] for a in audits] == [s["id"] for s in stored]
    after_first = {
        "provider": "groq",
        "model": model,
        "input_usd_per_mtok": "0.15",
        "output_usd_per_mtok": "0.60",
        "effective_from": "2026-10-19T16:00:00+00:00",
    }
    assert audits[0]["details"] == {"before": None, "after": after_first}
    assert audits[1]["details"]["before"] == after_first
    assert audits[1]["details"]["after"]["input_usd_per_mtok"] == "0.2"

    fx = await w.mutate(
        w.admin, FX, {"usd_to_myr": SEED_FX, "effective_from": "2026-10-21T00:00:00+08:00"}
    )
    assert fx.status_code == 201
    [fx_audit] = await w.q(
        "SELECT actor_id::text AS actor_id, client_id, details FROM console_audit"
        " WHERE action = 'billing.fx_added' AND details->'after'->>'effective_from' = %s",
        "2026-10-20T16:00:00+00:00",
    )
    assert (fx_audit["actor_id"], fx_audit["client_id"]) == (w.admin_id, None)
    assert fx_audit["details"]["after"] == {
        "usd_to_myr": SEED_FX,
        "effective_from": "2026-10-20T16:00:00+00:00",
    }
    assert fx_audit["details"]["before"]["usd_to_myr"] == SEED_FX


@requires_docker
@pytest.mark.asyncio
async def test_every_write_changes_the_memo_fingerprint(
    sessions: SessionWorld, pinned_clock: None
) -> None:
    w = sessions
    tenant = await company(w, "Fingerprint Sdn Bhd")

    async def current() -> tuple[Any, ...]:
        async with tx(w.pool, tenant) as conn:
            return await fingerprint(conn, uuid.UUID(tenant))

    seen = [await current()]
    writes = [
        (PRICES, price_body(model_name(), "2026-10-21T00:00:00+08:00")),
        (FX, {"usd_to_myr": SEED_FX, "effective_from": "2026-10-21T12:00:00+08:00"}),
        (
            plan_path(tenant),
            {
                "stage": "pilot",
                "bundle": "individual",
                "effective_from": "2026-10-05",
                "reason": "synthetic",
            },
        ),
    ]
    for path, body in writes:
        response = await w.mutate(w.admin, path, body)
        assert response.status_code == 201, response.text
        seen.append(await current())
    assert len(set(seen)) == len(seen)


# --- contract --------------------------------------------------------------------------------


@pytest.mark.parametrize("model", ADMIN_MODELS, ids=lambda m: m.__name__)
def test_admin_models_forbid_extra_fields(model: type[BaseModel]) -> None:
    assert model.model_config.get("extra") == "forbid"


def test_client_models_still_carry_no_cost_field_and_admin_models_are_not_on_client_routes() -> (
    None
):
    for model in CLIENT_MODELS:
        names = model.model_json_schema(mode="serialization")["properties"]
        assert [n for n in names if FORBIDDEN.search(n)] == [], model.__name__
    assert any(FORBIDDEN.search(n) for n in AdminCompanyOut.model_json_schema()["properties"])
    document = contract()
    client_routes = {p: v for p, v in document["paths"].items() if p.startswith(f"{BASE}/usage")}
    admin_names = {m.__name__ for m in ADMIN_MODELS}
    text = repr(client_routes)
    for name in admin_names:
        assert not re.search(rf"/{name}\b", text), name
    admin_routes = {p for p in document["paths"] if p.startswith(ADMIN)}
    assert admin_routes == {
        OVERVIEW,
        PRICES,
        FX,
        f"{ADMIN}/clients/{{client_id}}/plan",
        f"{ADMIN}/clients/{{client_id}}/langfuse-check",
    }
