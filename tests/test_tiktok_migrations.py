"""Migrations 0490-0493: legacy TikTok cleanup, one active store per shop, route, sync tables.

Runs against a disposable migrated Postgres with synthetic rows only (made-up shop ids).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from psycopg import errors

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.usefixtures("migrated_dsn")]

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
CLEANUP = MIGRATIONS / "0490_tiktok_legacy_cleanup.sql"
TIKTOK_RAW = '{"channel": "tiktok_shop"}'


@pytest.fixture
def owner(pg_container: object) -> Any:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        yield conn


def make_tenant(owner: psycopg.Connection[Any]) -> uuid.UUID:
    client = uuid.uuid4()
    owner.execute(
        "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,'Synthetic tiktok','t')",
        (client, f"tiktok-mig-{client}"),
    )
    return client


def make_store(
    owner: psycopg.Connection[Any],
    client: uuid.UUID,
    shop: str,
    *,
    channel: str = "tiktok_shop",
    disconnected: bool = False,
) -> uuid.UUID:
    store = uuid.uuid4()
    owner.execute(
        "INSERT INTO channel_credentials(id,client_id,channel,shop_id,disconnected_at) "
        "VALUES (%s,%s,%s,%s,CASE WHEN %s THEN now() END)",
        (store, client, channel, shop, disconnected),
    )
    return store


def make_endpoint(owner: psycopg.Connection[Any], client: uuid.UUID, store: uuid.UUID) -> uuid.UUID:
    endpoint = uuid.uuid4()
    owner.execute(
        "INSERT INTO webhook_endpoints(id,client_id,public_id,credential_id) VALUES (%s,%s,%s,%s)",
        (endpoint, client, uuid.uuid4().hex, store),
    )
    return endpoint


def make_event(
    owner: psycopg.Connection[Any], client: uuid.UUID, endpoint: uuid.UUID, raw: str = TIKTOK_RAW
) -> uuid.UUID:
    event = uuid.uuid4()
    owner.execute(
        "INSERT INTO ingress_events"
        "(id,client_id,endpoint_id,idempotency_key,payload_hash,trace_id,raw) "
        "VALUES (%s,%s,%s,%s,'h',%s,%s::jsonb)",
        (event, client, endpoint, uuid.uuid4().hex, uuid.uuid4(), raw),
    )
    return event


def make_grant(
    owner: psycopg.Connection[Any], client: uuid.UUID, store: uuid.UUID, shop: str
) -> None:
    owner.execute(
        "INSERT INTO ecommerce_grants(id,client_id,record_id,platform,shop_id,app_id,"
        "credential_generation,state,source,scopes,verified_at,expires_at) "
        "VALUES (%s,%s,%s,'tiktok_shop',%s,'SYNTH-APP',1,'active',"
        "'verified_authorization_exchange','{seller.order.info}',now(),now() + interval '1 hour')",
        (uuid.uuid4(), client, store, shop),
    )


def exists(owner: psycopg.Connection[Any], table: str, row_id: uuid.UUID) -> bool:
    query = cast(LiteralString, f"SELECT count(*) FROM {table} WHERE id=%s")
    row = owner.execute(query, (row_id,)).fetchone()
    assert row is not None
    return bool(row[0])


def event_status(owner: psycopg.Connection[Any], event: uuid.UUID) -> str:
    row = owner.execute("SELECT status FROM ingress_events WHERE id=%s", (event,)).fetchone()
    assert row is not None
    return str(row[0])


def test_legacy_unverified_tiktok_stores_removed_and_verified_kept(
    owner: psycopg.Connection[Any],
) -> None:
    client = make_tenant(owner)
    legacy_shop, kept_shop = f"SYNTH-{uuid.uuid4()}", f"SYNTH-{uuid.uuid4()}"
    legacy = make_store(owner, client, legacy_shop)
    kept = make_store(owner, client, kept_shop)
    make_grant(owner, client, kept, kept_shop)
    other = make_store(owner, client, f"SYNTH-{uuid.uuid4()}", channel="whatsapp")
    legacy_endpoint = make_endpoint(owner, client, legacy)
    kept_endpoint = make_endpoint(owner, client, kept)
    other_endpoint = make_endpoint(owner, client, other)
    legacy_event = make_event(owner, client, legacy_endpoint)
    kept_event = make_event(owner, client, kept_endpoint)
    other_event = make_event(owner, client, other_endpoint, '{"channel": "whatsapp"}')

    owner.execute(cast(LiteralString, CLEANUP.read_text()))

    assert not exists(owner, "channel_credentials", legacy)
    assert not exists(owner, "webhook_endpoints", legacy_endpoint)
    assert not exists(owner, "ingress_events", legacy_event)
    assert exists(owner, "channel_credentials", kept)
    assert exists(owner, "webhook_endpoints", kept_endpoint)
    assert exists(owner, "channel_credentials", other)
    assert event_status(owner, kept_event) == "dead"
    assert event_status(owner, other_event) == "received"


def test_second_active_store_for_same_shop_rejected(owner: psycopg.Connection[Any]) -> None:
    first, second = make_tenant(owner), make_tenant(owner)
    shop = f"SYNTH-{uuid.uuid4()}"
    make_store(owner, first, shop)
    with pytest.raises(errors.UniqueViolation):
        make_store(owner, second, shop)
    # Other channels are not constrained by the TikTok index.
    make_store(owner, second, shop, channel="shopee")


def test_disconnected_store_frees_the_shop(owner: psycopg.Connection[Any]) -> None:
    first, second = make_tenant(owner), make_tenant(owner)
    shop = f"SYNTH-{uuid.uuid4()}"
    old = make_store(owner, first, shop, disconnected=True)
    make_store(owner, second, shop)
    # Disconnecting the live store frees it again.
    live = make_store(owner, first, f"SYNTH-{uuid.uuid4()}")
    owner.execute("UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", (live,))
    assert exists(owner, "channel_credentials", old)


def test_disconnecting_fences_the_credential_and_its_grant(owner: psycopg.Connection[Any]) -> None:
    client = make_tenant(owner)
    shop = f"SYNTH-{uuid.uuid4()}"
    store = make_store(owner, client, shop)
    make_grant(owner, client, store, shop)
    owner.execute("UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", (store,))
    generation = owner.execute(
        "SELECT credential_generation FROM channel_credentials WHERE id=%s", (store,)
    ).fetchone()
    state = owner.execute(
        "SELECT state FROM ecommerce_grants WHERE record_id=%s", (store,)
    ).fetchone()
    assert generation == (2,)
    assert state == ("disconnected",)


def test_same_tenant_cannot_reuse_a_disconnected_shop_on_a_new_row_but_can_reconnect(
    owner: psycopg.Connection[Any],
) -> None:
    # The baseline UNIQUE (client_id, channel, shop_id) still covers disconnected rows, which keep
    # their shop_id. A same-tenant reconnect therefore reuses the existing row (later tasks clear
    # disconnected_at there); a first connect that lands on a stored shop fails as shop_in_use.
    client = make_tenant(owner)
    shop = f"SYNTH-{uuid.uuid4()}"
    old = make_store(owner, client, shop)
    owner.execute("UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", (old,))
    pending = uuid.uuid4()
    owner.execute(
        "INSERT INTO channel_credentials(id,client_id,channel) VALUES (%s,%s,'tiktok_shop')",
        (pending, client),
    )
    with pytest.raises(errors.UniqueViolation):
        owner.execute("UPDATE channel_credentials SET shop_id=%s WHERE id=%s", (shop, pending))
    owner.execute("UPDATE channel_credentials SET disconnected_at=NULL WHERE id=%s", (old,))
    row = owner.execute(
        "SELECT disconnected_at FROM channel_credentials WHERE id=%s", (old,)
    ).fetchone()
    assert row == (None,)


def test_grant_source_allows_token_refresh_only_besides_exchange(
    owner: psycopg.Connection[Any],
) -> None:
    client = make_tenant(owner)
    shop = f"SYNTH-{uuid.uuid4()}"
    store = make_store(owner, client, shop)
    columns = (
        "INSERT INTO ecommerce_grants(id,client_id,record_id,platform,shop_id,app_id,"
        "credential_generation,state,source,scopes,verified_at,expires_at) "
        "VALUES (%s,%s,%s,'tiktok_shop',%s,'SYNTH-APP',1,'active',%s,'{x}',now(),"
        "now() + interval '1 hour')"
    )
    owner.execute(columns, (uuid.uuid4(), client, store, shop, "verified_token_refresh"))
    with pytest.raises(errors.CheckViolation):
        owner.execute(columns, (uuid.uuid4(), client, uuid.uuid4(), shop, "anything_else"))


def test_resolve_route_returns_active_store_only_and_no_tokens(
    owner: psycopg.Connection[Any], app_dsn: str
) -> None:
    client = make_tenant(owner)
    live_shop, gone_shop = f"SYNTH-{uuid.uuid4()}", f"SYNTH-{uuid.uuid4()}"
    live = make_store(owner, client, live_shop)
    make_store(owner, client, gone_shop, disconnected=True)
    owner.execute(
        "UPDATE channel_credentials SET access_token='synthetic-ciphertext' WHERE id=%s", (live,)
    )
    with psycopg.connect(app_dsn) as conn:
        # No tenant context: the lookup is pre-tenant and SECURITY DEFINER.
        cursor = conn.execute("SELECT * FROM resolve_tiktok_shop_route(%s)", (live_shop,))
        names = [column.name for column in cursor.description or []]
        rows = cursor.fetchall()
        gone = conn.execute("SELECT * FROM resolve_tiktok_shop_route(%s)", (gone_shop,)).fetchall()
        unknown = conn.execute("SELECT * FROM resolve_tiktok_shop_route('NOPE')").fetchall()
    assert names == ["client_id", "store_id"]
    assert rows == [(client, live)]
    assert gone == []
    assert unknown == []


def test_resolve_route_is_not_executable_by_public_or_readonly(
    owner: psycopg.Connection[Any],
) -> None:
    row = owner.execute(
        "SELECT has_function_privilege('csa_app','resolve_tiktok_shop_route(text)','EXECUTE'), "
        "has_function_privilege('csa_ro','resolve_tiktok_shop_route(text)','EXECUTE'), "
        "(SELECT prosecdef FROM pg_proc WHERE proname='resolve_tiktok_shop_route'), "
        "(SELECT pg_get_userbyid(proowner) FROM pg_proc WHERE proname='resolve_tiktok_shop_route')"
    ).fetchone()
    assert row == (True, False, True, "csa")


def seed_rows(owner: psycopg.Connection[Any], client: uuid.UUID) -> uuid.UUID:
    store = make_store(owner, client, f"SYNTH-{uuid.uuid4()}")
    owner.execute(
        "INSERT INTO marketplace_store_state(store_id,client_id) VALUES (%s,%s)", (store, client)
    )
    owner.execute(
        "INSERT INTO marketplace_listings(client_id,store_id,channel,external_product_id,"
        "external_sku_id,product_name) VALUES (%s,%s,'tiktok_shop','P1',%s,'Synthetic item')",
        (client, store, f"SKU-{uuid.uuid4()}"),
    )
    owner.execute(
        "INSERT INTO marketplace_sync_jobs(client_id,store_id,kind) VALUES (%s,%s,'catch_up')",
        (client, store),
    )
    return store


def test_new_tables_isolate_tenants(owner: psycopg.Connection[Any], app_dsn: str) -> None:
    mine, other = make_tenant(owner), make_tenant(owner)
    store = seed_rows(owner, mine)
    tables = ["marketplace_store_state", "marketplace_listings", "marketplace_sync_jobs"]
    with psycopg.connect(app_dsn) as conn:
        for table in tables:
            query = cast(LiteralString, f"SELECT count(*) FROM {table}")
            conn.execute("SELECT set_config('app.client_id', %s, false)", (str(other),))
            assert conn.execute(query).fetchone() == (0,), table
            conn.execute("SELECT set_config('app.client_id', %s, false)", (str(mine),))
            assert conn.execute(query).fetchone() == (1,), table
        conn.execute("SELECT set_config('app.client_id', %s, false)", (str(other),))
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO marketplace_sync_jobs(client_id,store_id,kind) "
                "VALUES (%s,%s,'catch_up')",
                (mine, store),
            )
        conn.rollback()
        conn.execute("SELECT set_config('app.client_id', %s, false)", (str(other),))
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO marketplace_store_state(store_id,client_id) VALUES (%s,%s)",
                (store, mine),
            )
        conn.rollback()
        conn.execute("SELECT set_config('app.client_id', %s, false)", (str(other),))
        with pytest.raises(errors.InsufficientPrivilege):
            conn.execute(
                "INSERT INTO marketplace_listings(client_id,store_id,channel,external_product_id,"
                "external_sku_id,product_name) VALUES (%s,%s,'tiktok_shop','P','S','x')",
                (mine, store),
            )


def test_new_tables_have_rls_without_force_and_expected_grants(
    owner: psycopg.Connection[Any],
) -> None:
    rows = owner.execute(
        "SELECT relname, relrowsecurity, relforcerowsecurity, "
        "has_table_privilege('csa_app', oid, 'SELECT,INSERT,UPDATE,DELETE'), "
        "has_table_privilege('csa_ro', oid, 'SELECT'), "
        "has_table_privilege('csa_ro', oid, 'INSERT') "
        "FROM pg_class WHERE relname IN "
        "('marketplace_store_state','marketplace_listings','marketplace_sync_jobs') ORDER BY 1"
    ).fetchall()
    assert [row[0] for row in rows] == [
        "marketplace_listings",
        "marketplace_store_state",
        "marketplace_sync_jobs",
    ]
    assert all(row[1:] == (True, False, True, True, False) for row in rows)


def test_marketplace_order_upsert_key_is_unique(owner: psycopg.Connection[Any]) -> None:
    client, other = make_tenant(owner), make_tenant(owner)
    store = make_store(owner, client, f"SYNTH-{uuid.uuid4()}")
    insert = (
        "INSERT INTO orders(id,client_id,status,total,channel,external_order_id,store_id,"
        "provider_status,synced_at,provider_updated_at,buyer_cleared_at) "
        "VALUES (%s,%s,'processing',10,%s,%s,%s,%s,now(),now(),NULL)"
    )
    # The sync engine upserts by inference; ON CONSTRAINT cannot name a plain unique index.
    upsert = (
        insert + " ON CONFLICT (client_id, channel, external_order_id)"
        " WHERE channel IN ('tiktok_shop','shopee') AND external_order_id IS NOT NULL"
        " DO UPDATE SET provider_status = EXCLUDED.provider_status, synced_at = now()"
    )

    def args(
        tenant: uuid.UUID, channel: str, shop_store: uuid.UUID | None, status: str
    ) -> tuple[object, ...]:
        return (f"o-{uuid.uuid4()}", tenant, channel, "SYNTH-ORDER-1", shop_store, status)

    owner.execute(
        cast(LiteralString, upsert), args(client, "tiktok_shop", store, "AWAITING_SHIPMENT")
    )
    owner.execute(cast(LiteralString, upsert), args(client, "tiktok_shop", store, "DELIVERED"))
    rows = owner.execute(
        "SELECT provider_status FROM orders WHERE client_id=%s AND channel='tiktok_shop' "
        "AND external_order_id='SYNTH-ORDER-1'",
        (client,),
    ).fetchall()
    assert rows == [("DELIVERED",)]
    # A plain duplicate insert is still refused.
    with pytest.raises(errors.UniqueViolation):
        owner.execute(insert, args(client, "tiktok_shop", store, "PLAIN"))
    # The same external id is fine for another tenant, and for another channel.
    owner.execute(insert, args(other, "tiktok_shop", None, "AWAITING_SHIPMENT"))
    owner.execute(insert, args(client, "shopee", None, "AWAITING_SHIPMENT"))
    index = owner.execute(
        "SELECT indexdef FROM pg_indexes WHERE indexname='orders_marketplace_external_key'"
    ).fetchone()
    assert index is not None and "UNIQUE" in index[0] and "tiktok_shop" in index[0]


def test_listing_sku_is_unique_per_tenant_and_channel(owner: psycopg.Connection[Any]) -> None:
    client = make_tenant(owner)
    store = make_store(owner, client, f"SYNTH-{uuid.uuid4()}")
    insert = (
        "INSERT INTO marketplace_listings(client_id,store_id,channel,external_product_id,"
        "external_sku_id,product_name) VALUES (%s,%s,'tiktok_shop','P1','SYNTH-SKU','Item')"
    )
    owner.execute(insert, (client, store))
    with pytest.raises(errors.UniqueViolation):
        owner.execute(insert, (client, store))


def test_pending_sync_jobs_fold_duplicates_but_not_finished_ones(
    owner: psycopg.Connection[Any],
) -> None:
    client = make_tenant(owner)
    store = make_store(owner, client, f"SYNTH-{uuid.uuid4()}")
    insert = (
        "INSERT INTO marketplace_sync_jobs(client_id,store_id,kind,target,status) "
        "VALUES (%s,%s,%s,%s,%s)"
    )
    owner.execute(insert, (client, store, "order_refresh", "SYNTH-ORDER-1", "pending"))
    with pytest.raises(errors.UniqueViolation):
        owner.execute(insert, (client, store, "order_refresh", "SYNTH-ORDER-1", "pending"))
    # A whole-store job (NULL target) folds too, and a claimed or done one does not count.
    owner.execute(insert, (client, store, "catch_up", None, "pending"))
    with pytest.raises(errors.UniqueViolation):
        owner.execute(insert, (client, store, "catch_up", None, "pending"))
    owner.execute(insert, (client, store, "order_refresh", "SYNTH-ORDER-1", "claimed"))
    owner.execute(insert, (client, store, "order_refresh", "SYNTH-ORDER-1", "done"))
    owner.execute(insert, (client, store, "order_refresh", "SYNTH-ORDER-2", "pending"))
    with pytest.raises(errors.CheckViolation):
        owner.execute(insert, (client, store, "order_refresh", "SYNTH-ORDER-3", "running"))
    with pytest.raises(errors.CheckViolation):
        owner.execute(insert, (client, store, "write_back", None, "pending"))


def test_store_state_leaves_the_credential_unfenced(owner: psycopg.Connection[Any]) -> None:
    client = make_tenant(owner)
    store = seed_rows(owner, client)
    owner.execute(
        "UPDATE marketplace_store_state SET orders_synced_at=now(), catch_up_cursor=now(), "
        "last_error_code='rate_limited', backfill_cursor='SYNTH-PAGE-TOKEN' WHERE store_id=%s",
        (store,),
    )
    assert owner.execute(
        "SELECT backfill_cursor, problem_notified_at, products_synced_at "
        "FROM marketplace_store_state WHERE store_id=%s",
        (store,),
    ).fetchone() == ("SYNTH-PAGE-TOKEN", None, None)
    generation = owner.execute(
        "SELECT credential_generation FROM channel_credentials WHERE id=%s", (store,)
    ).fetchone()
    assert generation == (1,)


def test_max_applied_version_equals_expected(owner: psycopg.Connection[Any]) -> None:
    row = owner.execute("SELECT max(version) FROM schema_migrations").fetchone()
    assert row == (EXPECTED_SCHEMA_VERSION,)
    applied = {r[0] for r in owner.execute("SELECT version FROM schema_migrations").fetchall()}
    assert {"0490", "0491", "0492", "0493"} <= applied
