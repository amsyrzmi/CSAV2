"""Identity-only inventory migration checks on disposable PostgreSQL."""

from __future__ import annotations

from pathlib import Path
from typing import LiteralString, cast
from uuid import uuid4

import psycopg
import pytest
from psycopg import errors
from scripts import migrate

from tests.conftest import CSA_APP_PASSWORD, CSA_RO_PASSWORD, _app_dsn, requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]


async def _tenant(conn: psycopg.AsyncConnection) -> str:
    tenant = str(uuid4())
    await conn.execute(
        "INSERT INTO clients (id, slug, name, platform_version) "
        "VALUES (%s, %s, 'Synthetic inventory', 'test')",
        (tenant, f"inventory-{tenant}"),
    )
    return tenant


async def test_identity_catalog_and_empty_registry(migrated_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        cursor = await conn.execute("SELECT count(*) FROM inventory_warehouses")
        assert await cursor.fetchone() == (0,)
        cursor = await conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
            "WHERE oid = 'public.inventory_warehouses'::regclass"
        )
        assert await cursor.fetchone() == (True, False)
        cursor = await conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname IN ('products_client_id_id_key', 'orders_client_id_id_key', "
            "'order_items_client_id_id_product_id_key') ORDER BY conname"
        )
        assert await cursor.fetchall() == [
            ("order_items_client_id_id_product_id_key", "UNIQUE (client_id, id, product_id)"),
            ("orders_client_id_id_key", "UNIQUE (client_id, id)"),
            ("products_client_id_id_key", "UNIQUE (client_id, id)"),
        ]
        cursor = await conn.execute(
            "SELECT table_name, column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public' AND "
            "((table_name IN ('orders', 'products') AND column_name = 'id') OR "
            "(table_name = 'order_items' AND column_name IN ('id','order_id','product_id','qty'))) "
            "ORDER BY table_name, column_name"
        )
        assert await cursor.fetchall() == [
            ("order_items", "id", "uuid"),
            ("order_items", "order_id", "text"),
            ("order_items", "product_id", "uuid"),
            ("order_items", "qty", "integer"),
            ("orders", "id", "text"),
            ("products", "id", "uuid"),
        ]
        cursor = await conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename LIKE 'inventory_%' ORDER BY tablename"
        )
        assert await cursor.fetchall() == [("inventory_warehouses",)]
        cursor = await conn.execute(
            "SELECT count(*) FROM pg_class c, "
            "LATERAL aclexplode(c.relacl) a "
            "WHERE c.oid='public.inventory_warehouses'::regclass AND a.grantee=0"
        )
        assert await cursor.fetchone() == (0,)


async def test_warehouse_constraints_and_role_isolation(
    migrated_dsn: str, pg_container: object
) -> None:
    warehouse_a, warehouse_b = uuid4(), uuid4()
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        tenant_a, tenant_b = await _tenant(conn), await _tenant(conn)
        await conn.execute(
            "INSERT INTO inventory_warehouses (id, client_id, code, is_default) "
            "VALUES (%s,%s,'MAIN',true), (%s,%s,'MAIN',true)",
            (warehouse_a, tenant_a, warehouse_b, tenant_b),
        )
        for code, default, expected in [
            ("OTHER", True, errors.UniqueViolation),
            ("MAIN", False, errors.UniqueViolation),
            (" \t\n", False, errors.CheckViolation),
        ]:
            with pytest.raises(expected):
                async with conn.transaction():
                    await conn.execute(
                        "INSERT INTO inventory_warehouses (id,client_id,code,is_default) "
                        "VALUES (%s,%s,%s,%s)",
                        (uuid4(), tenant_a, code, default),
                    )
        with pytest.raises(errors.ForeignKeyViolation):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO inventory_warehouses (id,client_id,code,is_default) "
                    "VALUES (%s,%s,'ORPHAN',false)",
                    (uuid4(), uuid4()),
                )
    try:
        for role in ("csa_app", "csa_ro"):
            async with await psycopg.AsyncConnection.connect(
                _app_dsn(
                    pg_container,
                    role=role,
                    password=CSA_APP_PASSWORD if role == "csa_app" else CSA_RO_PASSWORD,
                )
            ) as conn:
                cursor = await conn.execute("SELECT id FROM inventory_warehouses")
                assert await cursor.fetchall() == []
                for tenant, expected in [(tenant_a, warehouse_a), (tenant_b, warehouse_b)]:
                    await conn.execute("SELECT set_config('app.client_id', %s, true)", (tenant,))
                    cursor = await conn.execute("SELECT id FROM inventory_warehouses")
                    assert await cursor.fetchall() == [(expected,)]
                    statements: list[LiteralString] = [
                        "INSERT INTO inventory_warehouses (id,client_id,code,is_default) "
                        "VALUES (gen_random_uuid(),"
                        "current_setting('app.client_id')::uuid,'NEW',false)",
                        "UPDATE inventory_warehouses SET code = 'CHANGED'",
                        "DELETE FROM inventory_warehouses",
                        "TRUNCATE inventory_warehouses",
                    ]
                    for statement in statements:
                        with pytest.raises(errors.InsufficientPrivilege):
                            async with conn.transaction():
                                await conn.execute(statement)
                cursor = await conn.execute(
                    "SELECT has_table_privilege(current_user, 'inventory_warehouses', 'SELECT'), "
                    "has_table_privilege(current_user, 'inventory_warehouses', 'INSERT'), "
                    "has_table_privilege(current_user, 'inventory_warehouses', 'UPDATE'), "
                    "has_table_privilege(current_user, 'inventory_warehouses', 'DELETE'), "
                    "has_table_privilege(current_user, 'inventory_warehouses', 'TRUNCATE'), "
                    "has_table_privilege(current_user, 'inventory_warehouses', 'REFERENCES'), "
                    "has_table_privilege(current_user, 'inventory_warehouses', 'TRIGGER')"
                )
                assert await cursor.fetchone() == (True, False, False, False, False, False, False)
    finally:
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute(
                "DELETE FROM inventory_warehouses WHERE client_id IN (%s,%s)",
                (tenant_a, tenant_b),
            )


async def test_legacy_stock_null_lines_and_global_keys_are_preserved(migrated_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        tenant_a, tenant_b = await _tenant(conn), await _tenant(conn)
        product, item = uuid4(), uuid4()
        order = f"synthetic-text-{uuid4()}"
        await conn.execute(
            "INSERT INTO products(id,client_id,sku,name,price,stock_qty) "
            "VALUES (%s,%s,'SYNTH','Synthetic',1,5)",
            (product, tenant_a),
        )
        await conn.execute(
            "INSERT INTO orders(id,client_id,status,total) VALUES (%s,%s,'draft',3)",
            (order, tenant_a),
        )
        await conn.execute(
            "INSERT INTO order_items"
            "(id,client_id,order_id,product_id,sku,name_snapshot,unit_price,qty) "
            "VALUES (%s,%s,%s,NULL,'SNAPSHOT','Synthetic',1,3)",
            (item, tenant_a, order),
        )
        with pytest.raises(errors.UniqueViolation):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO orders(id,client_id,status,total) VALUES (%s,%s,'draft',0)",
                    (order, tenant_b),
                )
        await conn.execute("UPDATE order_items SET product_id=%s WHERE id=%s", (product, item))
        root = Path(__file__).resolve().parents[1] / "db" / "sql" / "orders"
        for name, params, expected in [
            ("reserve_stock.sql", (3, product, tenant_a), (5, 3)),
            ("release_stock.sql", (3, product, tenant_a), (5, 0)),
            ("reserve_stock.sql", (3, product, tenant_a), (5, 3)),
            ("commit_stock.sql", (3, 3, product, tenant_a), (2, 0)),
        ]:
            await conn.execute((root / name).read_text(), params)
            cursor = await conn.execute(
                "SELECT stock_qty,reserved_qty FROM products WHERE id=%s", (product,)
            )
            assert await cursor.fetchone() == expected
        cursor = await conn.execute("SELECT count(*) FROM inventory_warehouses")
        assert await cursor.fetchone() == (0,)
        await conn.rollback()


async def test_runner_reapply_preserves_data_and_failure_rolls_back_ddl_and_stamp(
    migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MIGRATE_DATABASE_URL", migrated_dsn)
    monkeypatch.delenv("CSA_IDENTITY_PASSWORD", raising=False)
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        cursor = await conn.execute(
            "SELECT version,applied_at FROM schema_migrations ORDER BY version"
        )
        before = await cursor.fetchall()
        tenant = await _tenant(conn)
        warehouse = uuid4()
        await conn.execute(
            "INSERT INTO inventory_warehouses(id,client_id,code,is_default) "
            "VALUES (%s,%s,'REAPPLY',true)",
            (warehouse, tenant),
        )
    try:
        await migrate.run()
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            cursor = await conn.execute(
                "SELECT version,applied_at FROM schema_migrations ORDER BY version"
            )
            assert await cursor.fetchall() == before
            assert sum(row[0] == "0360" for row in before) == 1
            cursor = await conn.execute(
                "SELECT code,is_default FROM inventory_warehouses WHERE id=%s", (warehouse,)
            )
            assert await cursor.fetchone() == ("REAPPLY", True)
        failure = tmp_path / "9999_synthetic_failure.sql"
        failure.write_text(
            "CREATE TABLE public.synthetic_inventory_failure (id integer); SELECT 1/0;"
        )
        monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
        with pytest.raises(errors.DivisionByZero):
            await migrate.run()
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            cursor = await conn.execute("SELECT to_regclass('public.synthetic_inventory_failure')")
            assert await cursor.fetchone() == (None,)
            cursor = await conn.execute(
                "SELECT version FROM schema_migrations WHERE version='9999'"
            )
            assert await cursor.fetchall() == []
    finally:
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute("DELETE FROM public.inventory_warehouses WHERE id=%s", (warehouse,))


async def test_identity_upgrade_preserves_existing_synthetic_rows(migrated_dsn: str) -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "db/migrations/0360_inventory_identity.sql"
    ).read_text()
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        # Undo only this migration inside a rolled-back disposable test transaction.
        await conn.execute("DROP TABLE inventory_warehouses")
        await conn.execute("ALTER TABLE products DROP CONSTRAINT products_client_id_id_key")
        await conn.execute("ALTER TABLE orders DROP CONSTRAINT orders_client_id_id_key")
        await conn.execute(
            "ALTER TABLE order_items DROP CONSTRAINT order_items_client_id_id_product_id_key"
        )
        tenant_a, tenant_b = await _tenant(conn), await _tenant(conn)
        product, item = uuid4(), uuid4()
        order = f"synthetic-upgrade-{uuid4()}"
        await conn.execute(
            "INSERT INTO products(id,client_id,sku,name,price,stock_qty,reserved_qty) "
            "VALUES (%s,%s,'UPGRADE','Synthetic',1,9,2)",
            (product, tenant_a),
        )
        await conn.execute(
            "INSERT INTO orders(id,client_id,status,total) VALUES (%s,%s,'draft',2)",
            (order, tenant_a),
        )
        await conn.execute(
            "INSERT INTO order_items"
            "(id,client_id,order_id,product_id,sku,name_snapshot,unit_price,qty) "
            "VALUES (%s,%s,%s,%s,'UPGRADE','Synthetic',1,2)",
            (item, tenant_b, order, product),
        )
        query = (
            "SELECT p.stock_qty,p.reserved_qty,oi.client_id,oi.product_id,oi.order_id,oi.qty "
            "FROM products p JOIN order_items oi ON oi.product_id=p.id WHERE oi.id=%s"
        )
        cursor = await conn.execute(query, (item,))
        before = await cursor.fetchall()
        await conn.execute(cast(LiteralString, migration))
        cursor = await conn.execute(query, (item,))
        assert await cursor.fetchall() == before
        cursor = await conn.execute("SELECT count(*) FROM inventory_warehouses")
        assert await cursor.fetchone() == (0,)
        # Identity anchors do not silently introduce the deferred same-tenant FKs.
        await conn.execute("UPDATE order_items SET qty=3 WHERE id=%s", (item,))
        await conn.rollback()
