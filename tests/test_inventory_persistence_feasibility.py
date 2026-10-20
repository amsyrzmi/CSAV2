"""Disposable-Postgres feasibility checks for the proposed local inventory keys."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Literal

import psycopg
import pytest
from psycopg import errors

from tests.conftest import owner_dsn_for, requires_docker

SCHEMA = "inventory_feasibility"
TENANT_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PRODUCT_A = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
PRODUCT_B = "bbbbbbbb-1111-4111-8111-bbbbbbbbbbbb"
PRODUCT_C = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaac"
ORDER_A = "synthetic-order-a"
ORDER_B = "synthetic-order-b"
ITEM_A = "aaaaaaaa-3333-4333-8333-aaaaaaaaaaaa"
ITEM_B = "bbbbbbbb-3333-4333-8333-bbbbbbbbbbbb"
ITEM_C = "aaaaaaaa-3333-4333-8333-aaaaaaaaaaac"
ITEM_D = "aaaaaaaa-3333-4333-8333-aaaaaaaaaaad"
ITEM_E = "aaaaaaaa-3333-4333-8333-aaaaaaaaaaae"
WAREHOUSE_A = "aaaaaaaa-4444-4444-8444-aaaaaaaaaaaa"
REQUEST_A = "aaaaaaaa-5555-4555-8555-aaaaaaaaaaaa"
REQUEST_B = "aaaaaaaa-5555-4555-8555-aaaaaaaaaaab"


@pytest.fixture()
def inventory_db(pg_container: object) -> Iterator[str]:
    """Create and remove an isolated candidate schema in the disposable container."""
    dsn = owner_dsn_for(pg_container)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {SCHEMA}")
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.tenants (
                id uuid PRIMARY KEY
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.products (
                client_id uuid NOT NULL REFERENCES {SCHEMA}.tenants(id),
                id uuid NOT NULL,
                sku text NOT NULL,
                PRIMARY KEY (client_id, id),
                UNIQUE (client_id, sku)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.orders (
                client_id uuid NOT NULL REFERENCES {SCHEMA}.tenants(id),
                id text NOT NULL,
                PRIMARY KEY (client_id, id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.order_items (
                client_id uuid NOT NULL,
                id uuid NOT NULL,
                order_id text NOT NULL,
                product_id uuid,
                quantity integer NOT NULL CHECK (quantity > 0),
                PRIMARY KEY (client_id, id),
                UNIQUE (client_id, id, product_id),
                FOREIGN KEY (client_id, order_id)
                    REFERENCES {SCHEMA}.orders(client_id, id),
                FOREIGN KEY (client_id, product_id)
                    REFERENCES {SCHEMA}.products(client_id, id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.warehouses (
                client_id uuid NOT NULL REFERENCES {SCHEMA}.tenants(id),
                id uuid NOT NULL,
                PRIMARY KEY (client_id, id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.accounts (
                client_id uuid NOT NULL,
                product_id uuid NOT NULL,
                warehouse_id uuid NOT NULL,
                authority text NOT NULL CHECK (authority = 'csa_managed'),
                version bigint NOT NULL CHECK (version >= 0),
                on_hand integer NOT NULL CHECK (on_hand >= 0),
                safety_stock integer NOT NULL CHECK (safety_stock >= 0),
                PRIMARY KEY (client_id, product_id, warehouse_id),
                FOREIGN KEY (client_id, product_id)
                    REFERENCES {SCHEMA}.products(client_id, id),
                FOREIGN KEY (client_id, warehouse_id)
                    REFERENCES {SCHEMA}.warehouses(client_id, id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.claims (
                client_id uuid NOT NULL,
                order_item_id uuid NOT NULL,
                product_id uuid NOT NULL,
                warehouse_id uuid NOT NULL,
                quantity integer NOT NULL CHECK (quantity > 0),
                state text NOT NULL CHECK (
                    state IN ('active', 'cancelled', 'fulfilled')
                ),
                PRIMARY KEY (client_id, order_item_id),
                UNIQUE (client_id, order_item_id, product_id, warehouse_id),
                FOREIGN KEY (client_id, product_id, warehouse_id)
                    REFERENCES {SCHEMA}.accounts(client_id, product_id, warehouse_id),
                FOREIGN KEY (client_id, order_item_id, product_id)
                    REFERENCES {SCHEMA}.order_items(client_id, id, product_id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.returns (
                client_id uuid NOT NULL,
                request_id uuid NOT NULL,
                order_item_id uuid NOT NULL,
                product_id uuid NOT NULL,
                warehouse_id uuid NOT NULL,
                quantity integer NOT NULL CHECK (quantity > 0),
                received boolean NOT NULL DEFAULT false,
                sellable_quantity integer CHECK (
                    sellable_quantity >= 0 AND sellable_quantity <= quantity
                ),
                CHECK (sellable_quantity IS NULL OR received),
                PRIMARY KEY (client_id, request_id),
                FOREIGN KEY (client_id, order_item_id, product_id, warehouse_id)
                    REFERENCES {SCHEMA}.claims
                        (client_id, order_item_id, product_id, warehouse_id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.commands (
                client_id uuid NOT NULL,
                request_id uuid NOT NULL,
                product_id uuid NOT NULL,
                warehouse_id uuid NOT NULL,
                expected_version bigint NOT NULL,
                before_on_hand integer NOT NULL,
                proposed_on_hand integer NOT NULL,
                state text NOT NULL CHECK (
                    state IN ('pending', 'approved', 'rejected')
                ),
                review_reason text,
                PRIMARY KEY (client_id, request_id),
                FOREIGN KEY (client_id, product_id, warehouse_id)
                    REFERENCES {SCHEMA}.accounts(client_id, product_id, warehouse_id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE {SCHEMA}.events (
                id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
                client_id uuid NOT NULL,
                product_id uuid NOT NULL,
                warehouse_id uuid NOT NULL,
                event_kind text NOT NULL CHECK (
                    event_kind IN (
                        'reserved', 'cancelled', 'fulfilled', 'count_approved',
                        'return_requested', 'return_received', 'return_inspected'
                    )
                ),
                before_on_hand integer NOT NULL,
                after_on_hand integer NOT NULL,
                version_before bigint NOT NULL,
                version_after bigint NOT NULL,
                source_key text NOT NULL,
                UNIQUE (client_id, source_key),
                FOREIGN KEY (client_id, product_id, warehouse_id)
                    REFERENCES {SCHEMA}.accounts(client_id, product_id, warehouse_id)
            )
            """
        )

        conn.execute(
            f"INSERT INTO {SCHEMA}.tenants (id) VALUES (%s), (%s)",
            (TENANT_A, TENANT_B),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.products (client_id, id, sku) VALUES (%s, %s, 'SKU-A')",
            (TENANT_A, PRODUCT_A),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.products (client_id, id, sku) VALUES (%s, %s, 'SKU-B')",
            (TENANT_B, PRODUCT_B),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.products (client_id, id, sku) VALUES (%s, %s, 'SKU-C')",
            (TENANT_A, PRODUCT_C),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.orders (client_id, id) VALUES (%s, %s), (%s, %s)",
            (TENANT_A, ORDER_A, TENANT_B, ORDER_B),
        )
        conn.execute(
            f"""
            INSERT INTO {SCHEMA}.order_items
                (client_id, id, order_id, product_id, quantity)
            VALUES (%s, %s, %s, %s, 3), (%s, %s, %s, %s, 4),
                   (%s, %s, %s, %s, 4), (%s, %s, %s, %s, 2),
                   (%s, %s, %s, %s, 4)
            """,
            (
                TENANT_A,
                ITEM_A,
                ORDER_A,
                PRODUCT_A,
                TENANT_B,
                ITEM_B,
                ORDER_B,
                PRODUCT_B,
                TENANT_A,
                ITEM_C,
                ORDER_A,
                PRODUCT_A,
                TENANT_A,
                ITEM_D,
                ORDER_A,
                PRODUCT_A,
                TENANT_A,
                ITEM_E,
                ORDER_A,
                PRODUCT_A,
            ),
        )
        conn.execute(
            f"INSERT INTO {SCHEMA}.warehouses (client_id, id) VALUES (%s, %s)",
            (TENANT_A, WAREHOUSE_A),
        )
        conn.execute(
            f"""
            INSERT INTO {SCHEMA}.accounts
                (client_id, product_id, warehouse_id, authority, version, on_hand, safety_stock)
            VALUES (%s, %s, %s, 'csa_managed', 0, 5, 0)
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        )
        conn.execute(
            f"""
            INSERT INTO {SCHEMA}.accounts
                (client_id, product_id, warehouse_id, authority, version, on_hand, safety_stock)
            VALUES (%s, %s, %s, 'csa_managed', 0, 5, 0)
            """,
            (TENANT_A, PRODUCT_C, WAREHOUSE_A),
        )

    try:
        yield dsn
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {SCHEMA} CASCADE")


def _reserve(
    dsn: str,
    order_item_id: str,
    quantity: int,
    *,
    barrier: Barrier | None = None,
) -> bool:
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            if barrier is not None:
                barrier.wait(timeout=10)
            account = conn.execute(
                f"""
                SELECT version, on_hand, safety_stock
                FROM {SCHEMA}.accounts
                WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                FOR UPDATE
                """,
                (TENANT_A, PRODUCT_A, WAREHOUSE_A),
            ).fetchone()
            if account is None:
                raise AssertionError("synthetic inventory account is missing")
            version, on_hand, safety_stock = account
            line = conn.execute(
                f"""
                SELECT quantity FROM {SCHEMA}.order_items
                WHERE client_id = %s AND id = %s AND product_id = %s
                """,
                (TENANT_A, order_item_id, PRODUCT_A),
            ).fetchone()
            if line is None or line[0] != quantity:
                raise ValueError("claim must match a same-tenant order-item quantity")
            existing = conn.execute(
                f"""
                SELECT quantity, state FROM {SCHEMA}.claims
                WHERE client_id = %s AND order_item_id = %s
                    AND product_id = %s AND warehouse_id = %s
                """,
                (TENANT_A, order_item_id, PRODUCT_A, WAREHOUSE_A),
            ).fetchone()
            if existing is not None:
                if existing[0] != quantity:
                    raise ValueError("reservation replay payload differs")
                return existing[1] == "active"
            active = conn.execute(
                f"""
                SELECT coalesce(sum(quantity), 0)
                FROM {SCHEMA}.claims
                WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                    AND state = 'active'
                """,
                (TENANT_A, PRODUCT_A, WAREHOUSE_A),
            ).fetchone()
            if active is None:
                raise AssertionError("active reservation total is missing")
            if quantity > max(0, on_hand - active[0] - safety_stock):
                return False
            conn.execute(
                f"""
                INSERT INTO {SCHEMA}.claims
                    (client_id, order_item_id, product_id, warehouse_id, quantity, state)
                VALUES (%s, %s, %s, %s, %s, 'active')
                """,
                (TENANT_A, order_item_id, PRODUCT_A, WAREHOUSE_A, quantity),
            )
            conn.execute(
                f"""
                UPDATE {SCHEMA}.accounts
                SET version = version + 1
                WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                """,
                (TENANT_A, PRODUCT_A, WAREHOUSE_A),
            )
            conn.execute(
                f"""
                INSERT INTO {SCHEMA}.events (
                    client_id, product_id, warehouse_id, event_kind,
                    before_on_hand, after_on_hand, version_before, version_after, source_key
                ) VALUES (%s, %s, %s, 'reserved', %s, %s, %s, %s, %s)
                """,
                (
                    TENANT_A,
                    PRODUCT_A,
                    WAREHOUSE_A,
                    on_hand,
                    on_hand,
                    version,
                    version + 1,
                    f"reserve:{order_item_id}",
                ),
            )
        return True


class InjectedFailure(RuntimeError):
    pass


async def _return_event(
    conn: psycopg.AsyncConnection[tuple[object, ...]],
    tenant_id: str,
    request_id: str,
    kind: Literal["return_requested", "return_received", "return_inspected"],
    version: int,
    on_hand: int,
    sellable: int | None = None,
) -> None:
    await conn.execute(
        f"""
        INSERT INTO {SCHEMA}.events (
            client_id, product_id, warehouse_id, event_kind,
            before_on_hand, after_on_hand, version_before, version_after, source_key
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            tenant_id,
            PRODUCT_A,
            WAREHOUSE_A,
            kind,
            on_hand,
            on_hand + (sellable if sellable is not None else 0),
            version,
            version + (1 if sellable is not None else 0),
            f"{kind}:{request_id}",
        ),
    )


async def _request_return(
    dsn: str,
    request_id: str,
    quantity: int,
    *,
    tenant_id: str = TENANT_A,
    order_item_id: str = ITEM_A,
    barrier: asyncio.Barrier | None = None,
    fail_after: Literal["return", "event"] | None = None,
) -> bool:
    if quantity <= 0:
        raise ValueError("return quantity must be positive")
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.transaction():
        if barrier is not None:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        cursor = await conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            FOR UPDATE
            """,
            (tenant_id, PRODUCT_A, WAREHOUSE_A),
        )
        account = await cursor.fetchone()
        if account is None:
            raise ValueError("return account not found in tenant")
        cursor = await conn.execute(
            f"""
            SELECT order_item_id, quantity FROM {SCHEMA}.returns
            WHERE client_id = %s AND request_id = %s
            """,
            (tenant_id, request_id),
        )
        existing = await cursor.fetchone()
        if existing is not None:
            if (str(existing[0]), existing[1]) != (order_item_id, quantity):
                raise ValueError("return request payload differs")
            return True
        cursor = await conn.execute(
            f"""
            SELECT quantity, state FROM {SCHEMA}.claims
            WHERE client_id = %s AND order_item_id = %s
                AND product_id = %s AND warehouse_id = %s
            """,
            (tenant_id, order_item_id, PRODUCT_A, WAREHOUSE_A),
        )
        claim = await cursor.fetchone()
        if claim is None or claim[1] != "fulfilled":
            raise ValueError("return requires a same-tenant fulfilled claim")
        cursor = await conn.execute(
            f"""
            SELECT coalesce(sum(quantity), 0) FROM {SCHEMA}.returns
            WHERE client_id = %s AND order_item_id = %s
            """,
            (tenant_id, order_item_id),
        )
        returned = await cursor.fetchone()
        assert returned is not None
        if returned[0] + quantity > claim[0]:
            return False
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.returns (
                client_id, request_id, order_item_id, product_id, warehouse_id, quantity
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (tenant_id, request_id, order_item_id, PRODUCT_A, WAREHOUSE_A, quantity),
        )
        if fail_after == "return":
            raise InjectedFailure("after return")
        await _return_event(conn, tenant_id, request_id, "return_requested", account[0], account[1])
        if fail_after == "event":
            raise InjectedFailure("after event")
    return True


async def _review_return(
    dsn: str,
    request_id: str,
    action: Literal["receive", "inspect"],
    *,
    tenant_id: str = TENANT_A,
    sellable: int | None = None,
    barrier: asyncio.Barrier | None = None,
    fail_after: Literal["account", "return", "event"] | None = None,
) -> None:
    if (action == "inspect" and sellable is None) or (action == "receive" and sellable is not None):
        raise ValueError("only inspection requires a sellable quantity")
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.transaction():
        if barrier is not None:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        cursor = await conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            FOR UPDATE
            """,
            (tenant_id, PRODUCT_A, WAREHOUSE_A),
        )
        account = await cursor.fetchone()
        cursor = await conn.execute(
            f"""
            SELECT quantity, received, sellable_quantity FROM {SCHEMA}.returns
            WHERE client_id = %s AND request_id = %s
                AND product_id = %s AND warehouse_id = %s
            FOR UPDATE
            """,
            (tenant_id, request_id, PRODUCT_A, WAREHOUSE_A),
        )
        row = await cursor.fetchone()
        if account is None or row is None:
            raise ValueError("return not found in tenant")
        if action == "receive":
            if row[1]:
                return
            await conn.execute(
                f"""
                UPDATE {SCHEMA}.returns SET received = true
                WHERE client_id = %s AND request_id = %s
                """,
                (tenant_id, request_id),
            )
            kind = "return_received"
        else:
            assert sellable is not None
            if sellable < 0 or sellable > row[0]:
                raise ValueError("sellable quantity outside return quantity")
            if not row[1]:
                raise ValueError("inspection requires receipt")
            if row[2] is not None:
                if row[2] != sellable:
                    raise ValueError("inspection payload differs")
                return
            await conn.execute(
                f"""
                UPDATE {SCHEMA}.accounts
                SET on_hand = on_hand + %s, version = version + 1
                WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                """,
                (sellable, tenant_id, PRODUCT_A, WAREHOUSE_A),
            )
            if fail_after == "account":
                raise InjectedFailure("after account")
            await conn.execute(
                f"""
                UPDATE {SCHEMA}.returns SET sellable_quantity = %s
                WHERE client_id = %s AND request_id = %s
                """,
                (sellable, tenant_id, request_id),
            )
            kind = "return_inspected"
        if fail_after == "return":
            raise InjectedFailure("after return")
        await _return_event(conn, tenant_id, request_id, kind, account[0], account[1], sellable)
        if fail_after == "event":
            raise InjectedFailure("after event")


async def _return_snapshot(
    dsn: str, tenant_id: str = TENANT_A
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]], list[tuple[object, ...]]]:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cursor = await conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            """,
            (tenant_id, PRODUCT_A, WAREHOUSE_A),
        )
        accounts = await cursor.fetchall()
        cursor = await conn.execute(
            f"""
            SELECT request_id::text, quantity, received, sellable_quantity
            FROM {SCHEMA}.returns WHERE client_id = %s ORDER BY request_id
            """,
            (tenant_id,),
        )
        returns = await cursor.fetchall()
        cursor = await conn.execute(
            f"""
            SELECT event_kind, before_on_hand, after_on_hand,
                   version_before, version_after, source_key
            FROM {SCHEMA}.events WHERE client_id = %s
            ORDER BY version_after, source_key
            """,
            (tenant_id,),
        )
        return accounts, returns, await cursor.fetchall()


async def _finish_claim(
    dsn: str,
    state: Literal["cancelled", "fulfilled"],
    *,
    barrier: asyncio.Barrier | None = None,
    fail_after: Literal["account", "claim", "event"] | None = None,
) -> bool:
    async with await psycopg.AsyncConnection.connect(dsn) as conn, conn.transaction():
        if barrier is not None:
            await asyncio.wait_for(barrier.wait(), timeout=10)
        cursor = await conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            FOR UPDATE
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        )
        account = await cursor.fetchone()
        cursor = await conn.execute(
            f"""
            SELECT quantity, state FROM {SCHEMA}.claims
            WHERE client_id = %s AND order_item_id = %s
                AND product_id = %s AND warehouse_id = %s
            FOR UPDATE
            """,
            (TENANT_A, ITEM_A, PRODUCT_A, WAREHOUSE_A),
        )
        claim = await cursor.fetchone()
        if account is None or claim is None:
            raise AssertionError("synthetic account or claim is missing")
        if claim[1] != "active":
            return claim[1] == state
        decrement = claim[0] if state == "fulfilled" else 0
        await conn.execute(
            f"""
            UPDATE {SCHEMA}.accounts
            SET on_hand = on_hand - %s, version = version + 1
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            """,
            (decrement, TENANT_A, PRODUCT_A, WAREHOUSE_A),
        )
        if fail_after == "account":
            raise InjectedFailure("after account")
        await conn.execute(
            f"""
            UPDATE {SCHEMA}.claims SET state = %s
            WHERE client_id = %s AND order_item_id = %s
            """,
            (state, TENANT_A, ITEM_A),
        )
        if fail_after == "claim":
            raise InjectedFailure("after claim")
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.events (
                client_id, product_id, warehouse_id, event_kind,
                before_on_hand, after_on_hand, version_before, version_after, source_key
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                TENANT_A,
                PRODUCT_A,
                WAREHOUSE_A,
                state,
                account[1],
                account[1] - decrement,
                account[0],
                account[0] + 1,
                f"{state}:{ITEM_A}",
            ),
        )
        if fail_after == "event":
            raise InjectedFailure("after event")
    return True


async def _assert_claim_snapshot(
    dsn: str, state: Literal["active", "cancelled", "fulfilled"]
) -> None:
    async with await psycopg.AsyncConnection.connect(dsn) as conn:
        cursor = await conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        )
        assert await cursor.fetchone() == (
            1 if state == "active" else 2,
            2 if state == "fulfilled" else 5,
        )
        cursor = await conn.execute(
            f"SELECT quantity, state FROM {SCHEMA}.claims WHERE client_id = %s",
            (TENANT_A,),
        )
        assert await cursor.fetchall() == [(3, state)]
        cursor = await conn.execute(
            f"""
            SELECT event_kind, before_on_hand, after_on_hand,
                   version_before, version_after, source_key
            FROM {SCHEMA}.events WHERE client_id = %s ORDER BY version_after
            """,
            (TENANT_A,),
        )
        expected = [("reserved", 5, 5, 0, 1, f"reserve:{ITEM_A}")]
        if state != "active":
            expected.append((state, 5, 2 if state == "fulfilled" else 5, 1, 2, f"{state}:{ITEM_A}"))
        assert await cursor.fetchall() == expected


@requires_docker
@pytest.mark.parametrize("attempt", range(8))
async def test_cancel_fulfil_race_has_one_terminal_event_and_no_replay(
    inventory_db: str, attempt: int
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    barrier = asyncio.Barrier(2)
    states: list[Literal["cancelled", "fulfilled"]] = ["cancelled", "fulfilled"]
    if attempt % 2:
        states.reverse()
    results = await asyncio.wait_for(
        asyncio.gather(*(_finish_claim(inventory_db, state, barrier=barrier) for state in states)),
        timeout=20,
    )
    assert sorted(results) == [False, True]
    winner = states[results.index(True)]
    for state in states:
        assert await _finish_claim(inventory_db, state) == (state == winner)
    assert not _reserve(inventory_db, ITEM_A, 3)
    await _assert_claim_snapshot(inventory_db, winner)


@requires_docker
@pytest.mark.parametrize("state", ["cancelled", "fulfilled"])
async def test_terminal_replay_and_opposite_transition_leave_exact_state(
    inventory_db: str, state: Literal["cancelled", "fulfilled"]
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, state)
    assert await _finish_claim(inventory_db, state)
    opposite = "fulfilled" if state == "cancelled" else "cancelled"
    assert not await _finish_claim(inventory_db, opposite)
    assert not _reserve(inventory_db, ITEM_A, 3)
    await _assert_claim_snapshot(inventory_db, state)


@requires_docker
@pytest.mark.parametrize("state", ["cancelled", "fulfilled"])
@pytest.mark.parametrize("fail_after", ["account", "claim", "event"])
async def test_terminal_failure_at_each_write_rolls_back_and_allows_retry(
    inventory_db: str,
    state: Literal["cancelled", "fulfilled"],
    fail_after: Literal["account", "claim", "event"],
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    with pytest.raises(InjectedFailure, match=f"after {fail_after}"):
        await _finish_claim(inventory_db, state, fail_after=fail_after)
    await _assert_claim_snapshot(inventory_db, "active")
    assert await _finish_claim(inventory_db, state)
    assert await _finish_claim(inventory_db, state)
    await _assert_claim_snapshot(inventory_db, state)


@requires_docker
async def test_competing_return_requests_cannot_exceed_fulfilled_quantity(
    inventory_db: str,
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, "fulfilled")
    before = await _return_snapshot(inventory_db)
    barrier = asyncio.Barrier(2)
    ids = [REQUEST_A, REQUEST_B]
    results = await asyncio.wait_for(
        asyncio.gather(*(_request_return(inventory_db, key, 2, barrier=barrier) for key in ids)),
        timeout=20,
    )
    assert sorted(results) == [False, True]
    winner = ids[results.index(True)]
    assert await _request_return(inventory_db, winner, 2)
    accounts, returns, events = await _return_snapshot(inventory_db)
    assert accounts == before[0] == [(2, 2)]
    assert returns == [(winner, 2, False, None)]
    assert events == sorted(
        [*before[2], ("return_requested", 2, 2, 2, 2, f"return_requested:{winner}")],
        key=lambda row: (row[4], row[5]),
    )


@requires_docker
async def test_duplicate_return_request_race_records_one_request_and_event(
    inventory_db: str,
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, "fulfilled")
    before = await _return_snapshot(inventory_db)
    barrier = asyncio.Barrier(2)
    results = await asyncio.wait_for(
        asyncio.gather(
            *(_request_return(inventory_db, REQUEST_A, 3, barrier=barrier) for _ in range(2))
        ),
        timeout=20,
    )
    assert results == [True, True]
    accounts, returns, events = await _return_snapshot(inventory_db)
    assert accounts == before[0]
    assert returns == [(REQUEST_A, 3, False, None)]
    assert events == sorted(
        [*before[2], ("return_requested", 2, 2, 2, 2, f"return_requested:{REQUEST_A}")],
        key=lambda row: (row[4], row[5]),
    )


@requires_docker
async def test_conflicting_inspection_race_preserves_one_disposition(
    inventory_db: str,
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, "fulfilled")
    assert await _request_return(inventory_db, REQUEST_A, 3)
    await _review_return(inventory_db, REQUEST_A, "receive")
    before = await _return_snapshot(inventory_db)
    barrier = asyncio.Barrier(2)
    quantities = [1, 2]
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                _review_return(inventory_db, REQUEST_A, "inspect", sellable=qty, barrier=barrier)
                for qty in quantities
            ),
            return_exceptions=True,
        ),
        timeout=20,
    )
    assert results.count(None) == 1
    conflict = results[1 - results.index(None)]
    assert isinstance(conflict, ValueError)
    assert str(conflict) == "inspection payload differs"
    winner = quantities[results.index(None)]
    accounts, returns, events = await _return_snapshot(inventory_db)
    assert accounts == [(3, 2 + winner)]
    assert returns == [(REQUEST_A, 3, True, winner)]
    assert events == [
        *before[2],
        ("return_inspected", 2, 2 + winner, 2, 3, f"return_inspected:{REQUEST_A}"),
    ]


@requires_docker
@pytest.mark.parametrize("sellable", [0, 2, 3])
async def test_return_requires_receipt_and_duplicate_inspection_restocks_once(
    inventory_db: str, sellable: int
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, "fulfilled")
    assert await _request_return(inventory_db, REQUEST_A, 3)
    requested = await _return_snapshot(inventory_db)
    assert requested[0] == [(2, 2)]
    with pytest.raises(ValueError, match="requires receipt"):
        await _review_return(inventory_db, REQUEST_A, "inspect", sellable=sellable)
    assert await _return_snapshot(inventory_db) == requested
    await _review_return(inventory_db, REQUEST_A, "receive")
    await _review_return(inventory_db, REQUEST_A, "receive")
    received = await _return_snapshot(inventory_db)
    assert received[0] == requested[0]
    assert received[1] == [(REQUEST_A, 3, True, None)]
    barrier = asyncio.Barrier(2)
    await asyncio.wait_for(
        asyncio.gather(
            *(
                _review_return(
                    inventory_db, REQUEST_A, "inspect", sellable=sellable, barrier=barrier
                )
                for _ in range(2)
            )
        ),
        timeout=20,
    )
    await _review_return(inventory_db, REQUEST_A, "inspect", sellable=sellable)
    await _review_return(inventory_db, REQUEST_A, "receive")
    assert await _request_return(inventory_db, REQUEST_A, 3)
    accounts, returns, events = await _return_snapshot(inventory_db)
    assert accounts == [(3, 2 + sellable)]
    assert returns == [(REQUEST_A, 3, True, sellable)]
    assert events == [
        ("reserved", 5, 5, 0, 1, f"reserve:{ITEM_A}"),
        ("fulfilled", 5, 2, 1, 2, f"fulfilled:{ITEM_A}"),
        ("return_received", 2, 2, 2, 2, f"return_received:{REQUEST_A}"),
        ("return_requested", 2, 2, 2, 2, f"return_requested:{REQUEST_A}"),
        ("return_inspected", 2, 2 + sellable, 2, 3, f"return_inspected:{REQUEST_A}"),
    ]


@requires_docker
@pytest.mark.parametrize("fail_after", ["return", "event"])
async def test_return_request_failure_rolls_back_row_and_event(
    inventory_db: str, fail_after: Literal["return", "event"]
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, "fulfilled")
    before = await _return_snapshot(inventory_db)
    with pytest.raises(InjectedFailure, match=f"after {fail_after}"):
        await _request_return(inventory_db, REQUEST_A, 3, fail_after=fail_after)
    assert await _return_snapshot(inventory_db) == before
    assert await _request_return(inventory_db, REQUEST_A, 3)
    assert await _request_return(inventory_db, REQUEST_A, 3)
    after = await _return_snapshot(inventory_db)
    assert after[0] == before[0]
    assert after[1] == [(REQUEST_A, 3, False, None)]
    assert len(after[2]) == len(before[2]) + 1


@requires_docker
@pytest.mark.parametrize(
    ("action", "fail_after"),
    [
        ("receive", "return"),
        ("receive", "event"),
        ("inspect", "account"),
        ("inspect", "return"),
        ("inspect", "event"),
    ],
)
async def test_return_review_failure_rolls_back_and_retry_posts_once(
    inventory_db: str,
    action: Literal["receive", "inspect"],
    fail_after: Literal["account", "return", "event"],
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, "fulfilled")
    assert await _request_return(inventory_db, REQUEST_A, 3)
    if action == "inspect":
        await _review_return(inventory_db, REQUEST_A, "receive")
    before = await _return_snapshot(inventory_db)
    sellable = 2 if action == "inspect" else None
    with pytest.raises(InjectedFailure, match=f"after {fail_after}"):
        await _review_return(
            inventory_db, REQUEST_A, action, sellable=sellable, fail_after=fail_after
        )
    assert await _return_snapshot(inventory_db) == before
    await _review_return(inventory_db, REQUEST_A, action, sellable=sellable)
    await _review_return(inventory_db, REQUEST_A, action, sellable=sellable)
    accounts, returns, events = await _return_snapshot(inventory_db)
    assert accounts == ([(3, 4)] if action == "inspect" else [(2, 2)])
    assert returns == [(REQUEST_A, 3, True, sellable)]
    assert len(events) == len(before[2]) + 1
    expected = (
        ("return_inspected", 2, 4, 2, 3, f"return_inspected:{REQUEST_A}")
        if action == "inspect"
        else ("return_received", 2, 2, 2, 2, f"return_received:{REQUEST_A}")
    )
    assert expected in events


@requires_docker
async def test_return_payload_conflicts_and_invalid_quantities_preserve_state(
    inventory_db: str,
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    before = await _return_snapshot(inventory_db)
    with pytest.raises(ValueError, match="fulfilled claim"):
        await _request_return(inventory_db, REQUEST_A, 3)
    assert await _return_snapshot(inventory_db) == before
    assert await _finish_claim(inventory_db, "fulfilled")
    assert await _request_return(inventory_db, REQUEST_A, 3)
    before = await _return_snapshot(inventory_db)
    for quantity, item in [(2, ITEM_A), (3, ITEM_D)]:
        with pytest.raises(ValueError, match="request payload differs"):
            await _request_return(inventory_db, REQUEST_A, quantity, order_item_id=item)
    for quantity in [0, -1]:
        with pytest.raises(ValueError, match="must be positive"):
            await _request_return(inventory_db, REQUEST_B, quantity)
    assert await _return_snapshot(inventory_db) == before
    await _review_return(inventory_db, REQUEST_A, "receive")
    before = await _return_snapshot(inventory_db)
    for sellable in [-1, 4]:
        with pytest.raises(ValueError, match="outside return quantity"):
            await _review_return(inventory_db, REQUEST_A, "inspect", sellable=sellable)
    assert await _return_snapshot(inventory_db) == before
    await _review_return(inventory_db, REQUEST_A, "inspect", sellable=2)
    before = await _return_snapshot(inventory_db)
    with pytest.raises(ValueError, match="inspection payload differs"):
        await _review_return(inventory_db, REQUEST_A, "inspect", sellable=3)
    assert await _return_snapshot(inventory_db) == before


@requires_docker
async def test_same_return_and_source_ids_are_independent_between_tenants(
    inventory_db: str,
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)
    assert await _finish_claim(inventory_db, "fulfilled")
    assert await _request_return(inventory_db, REQUEST_A, 3)
    tenant_a = await _return_snapshot(inventory_db)
    with pytest.raises(ValueError, match="not found in tenant"):
        await _review_return(inventory_db, REQUEST_A, "receive", tenant_id=TENANT_B)
    with pytest.raises(ValueError, match="not found in tenant"):
        await _request_return(inventory_db, REQUEST_A, 3, tenant_id=TENANT_B)
    assert await _return_snapshot(inventory_db) == tenant_a
    async with await psycopg.AsyncConnection.connect(inventory_db) as conn:
        await conn.execute(
            f"INSERT INTO {SCHEMA}.products (client_id, id, sku) VALUES (%s, %s, 'SAME-ID')",
            (TENANT_B, PRODUCT_A),
        )
        await conn.execute(
            f"INSERT INTO {SCHEMA}.orders (client_id, id) VALUES (%s, %s)",
            (TENANT_B, ORDER_A),
        )
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.order_items
                (client_id, id, order_id, product_id, quantity)
            VALUES (%s, %s, %s, %s, 3)
            """,
            (TENANT_B, ITEM_A, ORDER_A, PRODUCT_A),
        )
        await conn.execute(
            f"INSERT INTO {SCHEMA}.warehouses (client_id, id) VALUES (%s, %s)",
            (TENANT_B, WAREHOUSE_A),
        )
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.accounts
                (client_id, product_id, warehouse_id, authority, version, on_hand, safety_stock)
            VALUES (%s, %s, %s, 'csa_managed', 2, 7, 0)
            """,
            (TENANT_B, PRODUCT_A, WAREHOUSE_A),
        )
        await conn.execute(
            f"""
            INSERT INTO {SCHEMA}.claims
                (client_id, order_item_id, product_id, warehouse_id, quantity, state)
            VALUES (%s, %s, %s, %s, 3, 'fulfilled')
            """,
            (TENANT_B, ITEM_A, PRODUCT_A, WAREHOUSE_A),
        )
    assert await _request_return(inventory_db, REQUEST_A, 3, tenant_id=TENANT_B)
    await _review_return(inventory_db, REQUEST_A, "receive", tenant_id=TENANT_B)
    await _review_return(inventory_db, REQUEST_A, "inspect", tenant_id=TENANT_B, sellable=1)
    tenant_b = await _return_snapshot(inventory_db, TENANT_B)
    assert tenant_b == (
        [(3, 8)],
        [(REQUEST_A, 3, True, 1)],
        [
            ("return_received", 7, 7, 2, 2, f"return_received:{REQUEST_A}"),
            ("return_requested", 7, 7, 2, 2, f"return_requested:{REQUEST_A}"),
            ("return_inspected", 7, 8, 2, 3, f"return_inspected:{REQUEST_A}"),
        ],
    )
    assert await _return_snapshot(inventory_db) == tenant_a
    await _review_return(inventory_db, REQUEST_A, "receive")
    await _review_return(inventory_db, REQUEST_A, "inspect", sellable=2)
    assert await _return_snapshot(inventory_db, TENANT_B) == tenant_b
    assert await _request_return(inventory_db, REQUEST_A, 3, tenant_id=TENANT_B)
    assert await _return_snapshot(inventory_db, TENANT_B) == tenant_b


@requires_docker
async def test_reservation_replay_does_not_reject_for_low_remaining_availability(
    inventory_db: str,
) -> None:
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_reserve, inventory_db, ITEM_A, 3, barrier=barrier) for _ in range(2)
        ]
        assert [future.result(timeout=20) for future in futures] == [True, True]
    assert _reserve(inventory_db, ITEM_A, 3)
    with pytest.raises(ValueError, match="same-tenant order-item quantity"):
        _reserve(inventory_db, ITEM_A, 2)
    await _assert_claim_snapshot(inventory_db, "active")


@requires_docker
def test_candidate_composite_keys_reject_cross_tenant_parent_relationships(
    inventory_db: str,
) -> None:
    with psycopg.connect(inventory_db) as conn, pytest.raises(errors.ForeignKeyViolation):
        conn.execute(
            f"""
            INSERT INTO {SCHEMA}.order_items
                (client_id, id, order_id, product_id, quantity)
            VALUES (%s, %s, %s, %s, 1)
            """,
            (TENANT_A, "aaaaaaaa-3333-4333-8333-aaaaaaaaaaab", ORDER_B, PRODUCT_A),
        )

    with psycopg.connect(inventory_db) as conn, pytest.raises(errors.ForeignKeyViolation):
        conn.execute(
            f"""
            INSERT INTO {SCHEMA}.claims
                (client_id, order_item_id, product_id, warehouse_id, quantity, state)
            VALUES (%s, %s, %s, %s, 1, 'active')
            """,
            (TENANT_A, ITEM_A, PRODUCT_C, WAREHOUSE_A),
        )


@requires_docker
def test_competing_reservations_serialize_and_preserve_available_stock(
    inventory_db: str,
) -> None:
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(_reserve, inventory_db, item_id, 4, barrier=barrier)
            for item_id in (ITEM_C, ITEM_E)
        ]
        results = [future.result(timeout=20) for future in futures]

    assert sorted(results) == [False, True]
    with psycopg.connect(inventory_db) as conn:
        account = conn.execute(
            f"""
            SELECT version, on_hand
            FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        ).fetchone()
        claims = conn.execute(
            f"""
            SELECT count(*), coalesce(sum(quantity), 0)
            FROM {SCHEMA}.claims
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                AND state = 'active'
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        ).fetchone()
        event_count = conn.execute(
            f"SELECT count(*) FROM {SCHEMA}.events WHERE event_kind = 'reserved'"
        ).fetchone()

    assert account == (1, 5)
    assert claims == (1, 4)
    assert event_count == (1,)


@requires_docker
def test_stale_count_approval_is_rejected_without_erasing_new_claim(
    inventory_db: str,
) -> None:
    with psycopg.connect(inventory_db) as conn:
        conn.execute(
            f"""
            INSERT INTO {SCHEMA}.commands (
                client_id, request_id, product_id, warehouse_id,
                expected_version, before_on_hand, proposed_on_hand, state
            ) VALUES (%s, %s, %s, %s, 0, 5, 2, 'pending')
            """,
            (TENANT_A, REQUEST_A, PRODUCT_A, WAREHOUSE_A),
        )

    assert _reserve(inventory_db, ITEM_D, 2)
    with psycopg.connect(inventory_db) as conn, conn.transaction():
        account = conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            FOR UPDATE
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        ).fetchone()
        command = conn.execute(
            f"""
            SELECT expected_version, state FROM {SCHEMA}.commands
            WHERE client_id = %s AND request_id = %s
            FOR UPDATE
            """,
            (TENANT_A, REQUEST_A),
        ).fetchone()
        if account is None or command is None:
            raise AssertionError("synthetic approval state is missing")
        if account[0] != command[0] or command[1] != "pending":
            conn.execute(
                f"""
                UPDATE {SCHEMA}.commands
                SET state = 'rejected', review_reason = 'stale_version'
                WHERE client_id = %s AND request_id = %s
                """,
                (TENANT_A, REQUEST_A),
            )
        else:
            conn.execute(
                f"""
                UPDATE {SCHEMA}.accounts SET on_hand = 2, version = version + 1
                WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                """,
                (TENANT_A, PRODUCT_A, WAREHOUSE_A),
            )
            conn.execute(
                f"""
                UPDATE {SCHEMA}.commands SET state = 'approved'
                WHERE client_id = %s AND request_id = %s
                """,
                (TENANT_A, REQUEST_A),
            )

    with psycopg.connect(inventory_db) as conn:
        account = conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        ).fetchone()
        command = conn.execute(
            f"""
            SELECT state, review_reason FROM {SCHEMA}.commands
            WHERE client_id = %s AND request_id = %s
            """,
            (TENANT_A, REQUEST_A),
        ).fetchone()
        active = conn.execute(
            f"""
            SELECT coalesce(sum(quantity), 0) FROM {SCHEMA}.claims
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                AND state = 'active'
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        ).fetchone()

    assert account == (1, 5)
    assert command == ("rejected", "stale_version")
    assert active == (2,)


@requires_docker
def test_event_constraint_failure_rolls_back_fulfilment_atomically(
    inventory_db: str,
) -> None:
    assert _reserve(inventory_db, ITEM_A, 3)

    with pytest.raises(errors.CheckViolation):
        with psycopg.connect(inventory_db) as conn, conn.transaction():
            account = conn.execute(
                f"""
                SELECT version, on_hand FROM {SCHEMA}.accounts
                WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                FOR UPDATE
                """,
                (TENANT_A, PRODUCT_A, WAREHOUSE_A),
            ).fetchone()
            if account is None:
                raise AssertionError("synthetic inventory account is missing")
            conn.execute(
                f"""
                UPDATE {SCHEMA}.accounts SET on_hand = on_hand - 3, version = version + 1
                WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
                """,
                (TENANT_A, PRODUCT_A, WAREHOUSE_A),
            )
            conn.execute(
                f"""
                UPDATE {SCHEMA}.claims SET state = 'fulfilled'
                WHERE client_id = %s AND order_item_id = %s AND state = 'active'
                """,
                (TENANT_A, ITEM_A),
            )
            conn.execute(
                f"""
                INSERT INTO {SCHEMA}.events (
                    client_id, product_id, warehouse_id, event_kind,
                    before_on_hand, after_on_hand, version_before, version_after, source_key
                ) VALUES (%s, %s, %s, 'invalid_event', %s, %s, %s, %s, 'fulfil:rollback')
                """,
                (
                    TENANT_A,
                    PRODUCT_A,
                    WAREHOUSE_A,
                    account[1],
                    account[1] - 3,
                    account[0],
                    account[0] + 1,
                ),
            )

    with psycopg.connect(inventory_db) as conn:
        account = conn.execute(
            f"""
            SELECT version, on_hand FROM {SCHEMA}.accounts
            WHERE client_id = %s AND product_id = %s AND warehouse_id = %s
            """,
            (TENANT_A, PRODUCT_A, WAREHOUSE_A),
        ).fetchone()
        claim_state = conn.execute(
            f"SELECT state FROM {SCHEMA}.claims WHERE client_id = %s AND order_item_id = %s",
            (TENANT_A, ITEM_A),
        ).fetchone()
        events = conn.execute(
            f"SELECT count(*) FROM {SCHEMA}.events WHERE client_id = %s",
            (TENANT_A,),
        ).fetchone()

    assert account == (1, 5)
    assert claim_state == ("active",)
    assert events == (1,)
