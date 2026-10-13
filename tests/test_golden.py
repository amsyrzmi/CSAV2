"""LG08 golden cases: whole turns on real Postgres with cassette model answers."""

from __future__ import annotations

import time
from decimal import Decimal

import psycopg
import pytest
from apps.worker.main import IngressWorker
from csa.core import db
from csa.graph.coordinator import build_runtime
from csa.graph.events import PostgresEventSink
from csa.graph.saver import build_checkpoint_pool, build_saver
from csa.turn.deps import PostgresTurnDependencies, build_llm_provider

from tests.conftest import owner_dsn_for, requires_docker
from tests.golden.runner import (
    CassetteGroq,
    Expect,
    GoldenCase,
    Order,
    Product,
    Seed,
    Turn,
    check,
    enqueue_turn,
    load_cases,
    observe,
    prepare_tap,
    seed_tenant,
    tap_action,
)
from tests.test_worker import _configure_test_settings

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.usefixtures("lg_setup_dsn")]

CASES = load_cases()


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
async def test_golden(
    case: GoldenCase, pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    tenant = seed_tenant(owner_dsn, case)
    groq = CassetteGroq()
    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    checkpoint_pool = build_checkpoint_pool(app_dsn)
    for pool in (owner_pool, app_pool, checkpoint_pool):
        await pool.open(wait=True)
    try:
        deps = PostgresTurnDependencies(app_pool, build_llm_provider(groq))
        worker = IngressWorker(
            owner_pool,
            app_pool,
            lock_conninfo=owner_dsn,
            coordinator=build_runtime(
                deps, build_saver(checkpoint_pool), PostgresEventSink(app_pool)
            ),
        )
        escalations = 0
        previous = []
        for index, turn in enumerate(case.turns):
            groq.load(turn.cassette)
            action_id = tap_action(turn.tap, previous) if turn.tap else None
            sender = (
                prepare_tap(owner_dsn, tenant, turn.tap, action_id)
                if turn.tap is not None and action_id is not None
                else tenant
            )
            trace_id = enqueue_turn(owner_dsn, sender, case, turn, index, action_id)
            assert await worker.process_once() is True
            seen = observe(owner_dsn, tenant, trace_id, action_id)
            check(turn.expect, seen, groq, escalations)
            escalations = seen.escalations
            previous.append(seen)
    finally:
        await groq.close()
        for pool in (owner_pool, app_pool, checkpoint_pool):
            await pool.close()


async def test_two_tenant_seed_timing_and_isolation(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from csa.core.tenancy import tx

    owner_dsn = owner_dsn_for(pg_container)
    _configure_test_settings(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    cases = [
        GoldenCase(
            id=f"seed-timing-{label}",
            matrix="Seed",
            description="Synthetic seed timing",
            seed=Seed(
                policy={"deployment_mode": mode},
                products=[
                    Product(sku="S1", name=f"Tenant {label} product", price=Decimal("10.50"))
                ],
                orders=[Order(id=f"GOLD-SEED-{label}", status="shipped", total=Decimal("10.50"))],
            ),
            turns=[Turn(say="hello", expect=Expect())],
        )
        for label, mode in (("A", "suggest"), ("B", "auto"))
    ]
    started = time.perf_counter()
    tenants = [seed_tenant(owner_dsn, case) for case in cases]
    elapsed = time.perf_counter() - started
    pool = db.build_pool(app_dsn, max_size=2)
    await pool.open(wait=True)
    try:
        for tenant, case in zip(tenants, cases, strict=True):
            async with tx(pool, str(tenant.client_id)) as conn:
                rows = await conn.execute("SELECT client_id,id FROM orders ORDER BY id")
                assert await rows.fetchall() == [
                    {"client_id": tenant.client_id, "id": case.seed.orders[0].id}
                ]
                rows = await conn.execute("SELECT client_id,name FROM products")
                assert await rows.fetchall() == [
                    {"client_id": tenant.client_id, "name": case.seed.products[0].name}
                ]
    finally:
        await pool.close()
    with psycopg.connect(owner_dsn) as conn:
        rows = conn.execute(
            "SELECT config->'policy'->>'deployment_mode' FROM clients "
            "WHERE id IN (%s,%s) ORDER BY slug",
            (tenants[0].client_id, tenants[1].client_id),
        ).fetchall()
    assert rows == [("suggest",), ("auto",)]
    print(f"LG08_SEED tenants=2 seconds={elapsed:.3f} tenant_isolation=passed")
    assert elapsed < 5, f"two synthetic tenant scenarios took {elapsed:.3f}s"
