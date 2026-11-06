"""M6 local read models on disposable synthetic tenants; never call marketplaces."""

import json
from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest
from csa.agents.ecommerce.authorization import PRODUCT_SCOPES

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import MACHINE_TOKEN, SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    """Both tenants hold orders_products so isolation checks get past the package gate."""
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "orders_products")


async def seed(w: SessionWorld, tenant: str) -> tuple[str, str]:
    order_id = f"synthetic-{uuid4()}"
    product_id = str(uuid4())
    await w.q(
        "INSERT INTO orders (id,client_id,status,total,tracking_no,carrier,ship_to) "
        "VALUES (%s,%s,'shipped',12.34,'SYNTHETIC-TRACK','Synthetic Carrier',"
        '\'{"private_address":"not-for-console"}\')',
        order_id,
        tenant,
    )
    await w.q(
        "INSERT INTO products (id,client_id,sku,name,price,stock_qty,reserved_qty,attributes) "
        "VALUES (%s,%s,%s,'Synthetic Product',12.34,9,2,"
        '\'{"private_supplier":"not-for-console"}\')',
        product_id,
        tenant,
        f"SYNTH-{product_id}",
    )
    return order_id, product_id


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff"])
async def test_business_staff_reads_and_isolation(sessions: SessionWorld, role: str) -> None:
    w = sessions
    order_id, product_id = await seed(w, w.tenants[0])
    foreign_order, foreign_product = await seed(w, w.tenants[1])
    member, _ = await w.member(role=role)
    for resource, record_id, foreign_id in (
        ("orders", order_id, foreign_order),
        ("products", product_id, foreign_product),
    ):
        base = f"/console/v1/business/{resource}"
        response = await member.get(base)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["marketplace_sync"] == "not_connected"
        assert [item["id"] for item in body["items"]] == [record_id]
        assert "not-for-console" not in response.text
        detail = await member.get(f"{base}/{record_id}")
        assert detail.status_code == 200
        if resource == "orders":
            assert detail.json()["total"] == "12.34"
            assert detail.json()["tracking_no"] == "SYNTHETIC-TRACK"
        else:
            assert detail.json()["available_qty"] == 7
        assert (await member.get(f"{base}/{foreign_id}")).status_code == 404
        assert (await member.get(f"{base}?before={foreign_id}")).status_code == 404
        assert (await member.get(f"{base}?client_id={w.tenants[1]}")).status_code == 403
        assert (await w.mutate(member, base, {})).status_code == 405


async def test_business_reader_machine_anonymous_denied(sessions: SessionWorld) -> None:
    w = sessions
    order_id, product_id = await seed(w, w.tenants[0])
    reader, _ = await w.member(role="read_only")
    anonymous = await w.browser()
    suffixes = (
        "orders",
        f"orders/{order_id}",
        "products",
        f"products/{product_id}",
        "stores",
        f"stores/{uuid4()}",
    )
    for suffix in suffixes:
        path = f"/console/v1/business/{suffix}"
        assert (await reader.get(path)).status_code == 403
        assert (await anonymous.get(path)).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="https://console.example.test"
    ) as machine:
        for suffix in suffixes:
            assert (
                await machine.get(
                    f"/console/v1/business/{suffix}",
                    headers={
                        "Authorization": f"Bearer {MACHINE_TOKEN}",
                        "X-Client-Id": w.tenants[0],
                    },
                )
            ).status_code == 403


async def test_business_keyset_bounds_and_admin_selection(sessions: SessionWorld) -> None:
    w = sessions
    records = [await seed(w, w.tenants[0]) for _ in range(3)]
    for resource, index in (("orders", 0), ("products", 1)):
        path = f"/console/v1/business/{resource}"
        assert (await w.admin.get(path)).status_code == 400
        url = f"{path}?client_id={w.tenants[0]}"
        seen = []
        cursor = None
        for _ in range(3):
            response = await w.admin.get(url + "&limit=1" + (f"&before={cursor}" if cursor else ""))
            assert response.status_code == 200, response.text
            page = response.json()
            seen.append(page["items"][0]["id"])
            cursor = page["next_cursor"]
        assert set(seen) == {record[index] for record in records}
        assert len(set(seen)) == 3 and cursor is None
        assert (await w.admin.get(url + "&limit=0")).status_code == 422
        assert (await w.admin.get(url + "&limit=51")).status_code == 422
        empty = await w.admin.get(f"{path}?client_id={w.tenants[1]}")
        assert empty.json()["items"] == [] and empty.json()["next_cursor"] is None
    assert (
        await w.admin.get(f"/console/v1/business/products?client_id={w.tenants[0]}&before=bad")
    ).status_code == 422


@pytest.mark.parametrize("role", ["platform_admin", "tenant_admin", "tenant_staff"])
async def test_business_order_source_store_filters_and_cursors(
    sessions: SessionWorld, role: str
) -> None:
    w = sessions
    shop = "SYNTH/store+?&=%"
    rows: list[str] = []
    for tenant, channel, store in (
        (w.tenants[0], "tiktok_shop", shop),
        (w.tenants[0], "tiktok_shop", shop),
        (w.tenants[0], "tiktok_shop", "SYNTH-OTHER-STORE"),
        (w.tenants[0], "shopee", shop),
        (w.tenants[0], "tiktok_shop", None),
        (w.tenants[0], "manual", None),
        (w.tenants[1], "tiktok_shop", shop),
        (w.tenants[1], "tiktok_shop", "FOREIGN-STORE"),
    ):
        order_id, _ = await seed(w, tenant)
        rows.append(order_id)
        await w.q(
            "UPDATE orders SET channel=%s, external_shop_id=%s, "
            "created_at='2026-01-01T00:00:00Z' WHERE id=%s AND client_id=%s",
            channel,
            store,
            order_id,
            tenant,
        )
    if role == "platform_admin":
        member = w.admin
        tenant_query = {"client_id": w.tenants[0]}
    else:
        member, _ = await w.member(role=role)
        tenant_query = {}

    def path(**params: str | int) -> str:
        return "/console/v1/business/orders?" + urlencode({**tenant_query, **params})

    all_orders = await member.get(path())
    assert all_orders.status_code == 200, all_orders.text
    assert {row["id"] for row in all_orders.json()["items"]} == set(rows[:6])
    platform_only = await member.get(path(channel="tiktok_shop"))
    assert {row["id"] for row in platform_only.json()["items"]} == {
        rows[0],
        rows[1],
        rows[2],
        rows[4],
    }
    seen = []
    cursor = None
    for index in range(2):
        params: dict[str, str | int] = {"channel": "tiktok_shop", "shop_id": shop, "limit": 1}
        if cursor:
            params["before"] = cursor
        page_response = await member.get(path(**params))
        assert page_response.status_code == 200, page_response.text
        page = page_response.json()
        assert page["source"] == "local_records" and page["marketplace_sync"] == "not_connected"
        assert len(page["items"]) == 1
        assert page["items"][0]["channel"] == "tiktok_shop"
        assert page["items"][0]["external_shop_id"] == shop
        seen.append(page["items"][0]["id"])
        cursor = page["next_cursor"]
        assert (cursor is not None) == (index == 0)
    assert seen == sorted(rows[:2], reverse=True), "equal timestamps must use the ID tie-breaker"
    for wrong_cursor in (rows[2], rows[3], rows[4], rows[6]):
        response = await member.get(path(channel="tiktok_shop", shop_id=shop, before=wrong_cursor))
        assert response.status_code == 404
    for store in ("FOREIGN-STORE", "MISSING-STORE", "' OR TRUE --"):
        empty = await member.get(path(channel="tiktok_shop", shop_id=store))
        assert empty.status_code == 200
        assert empty.json()["items"] == [] and empty.json()["next_cursor"] is None
    detail = f"/console/v1/business/orders/{rows[0]}?"
    assert (
        await member.get(
            detail + urlencode({**tenant_query, "channel": "tiktok_shop", "shop_id": shop})
        )
    ).status_code == 200
    for filters in (
        {"channel": "shopee", "shop_id": shop},
        {"channel": "tiktok_shop", "shop_id": "SYNTH-OTHER-STORE"},
    ):
        assert (
            await member.get(detail + urlencode({**tenant_query, **filters}))
        ).status_code == 404
    # A cursor that no longer belongs to the selected store cannot silently advance the page.
    await w.q("UPDATE orders SET external_shop_id='MOVED-STORE' WHERE id=%s", seen[0])
    assert (
        await member.get(path(channel="tiktok_shop", shop_id=shop, before=seen[0]))
    ).status_code == 404


async def test_business_order_filter_validation(sessions: SessionWorld) -> None:
    w = sessions
    order_id, _ = await seed(w, w.tenants[0])
    for suffix in ("orders", f"orders/{order_id}"):
        base = f"/console/v1/business/{suffix}?"
        for params, status in (
            ({"shop_id": "SYNTH-STORE"}, 400),
            ({"channel": "lazada"}, 422),
            ({"channel": ""}, 422),
            ({"channel": "tiktok_shop", "shop_id": ""}, 422),
            ({"channel": "tiktok_shop", "shop_id": "x" * 101}, 422),
        ):
            response = await w.admin.get(base + urlencode({"client_id": w.tenants[0], **params}))
            assert response.status_code == status, response.text
        for channel in ("telegram", "web", "tiktok_shop", "shopee", "manual"):
            response = await w.admin.get(
                base + urlencode({"client_id": w.tenants[0], "channel": channel})
            )
            assert response.status_code in (200, 404)


@pytest.mark.parametrize("role", ["platform_admin", "tenant_admin", "tenant_staff"])
async def test_business_store_records_and_exact_binding(sessions: SessionWorld, role: str) -> None:
    w = sessions
    # Migration 0491: an active TikTok shop belongs to one tenant, so ids are unique per run.
    shop = f"SYNTH/shop+?&=%-{uuid4()}"
    records = []
    for tenant, platform, shop_id, expiry in (
        (w.tenants[0], "tiktok_shop", shop, "2100-01-01T00:00:00Z"),
        (w.tenants[0], "shopee", shop, "2000-01-01T00:00:00Z"),
        (w.tenants[0], "lazada", None, None),
        (w.tenants[0], "tiktok_shop", None, None),
        (w.tenants[0], "telegram", shop, None),
        (w.tenants[1], "tiktok_shop", f"{shop}-other-tenant", None),
    ):
        record_id = str(uuid4())
        await w.q(
            "INSERT INTO channel_credentials "
            "(id,client_id,channel,shop_id,expires_at,access_token,refresh_token,meta) "
            "VALUES (%s,%s,%s,%s,%s,'synthetic-private-access','synthetic-private-refresh',"
            '\'{"scopes":["synthetic-private-scope"],"app_secret":"synthetic-private-secret"}\')',
            record_id,
            tenant,
            platform,
            shop_id,
            expiry,
        )
        records.append(record_id)
    if role == "platform_admin":
        member = w.admin
        query = {"client_id": w.tenants[0]}
    else:
        member, _ = await w.member(role=role)
        query = {}
    base = "/console/v1/business/stores"

    def path(suffix: str = "", /, **params: str | int) -> str:
        return f"{base}{suffix}?" + urlencode({**query, **params})

    response = await member.get(path())
    assert response.status_code == 200, response.text
    assert "synthetic-private" not in response.text
    body = response.json()
    assert body["source"] == "local_credential_records"
    assert body["marketplace_sync"] == "not_connected"
    assert {row["id"] for row in body["items"]} == set(records[:4])
    assert {row["id"]: row["expiry"] for row in body["items"]} == {
        records[0]: "not_expired",
        records[1]: "expired",
        records[2]: "unknown",
        records[3]: "unknown",
    }
    for row in body["items"]:
        assert set(row) == {
            "id",
            "platform",
            "shop_id",
            "expires_at",
            "updated_at",
            "expiry",
            "authorization",
            "permissions",
            "provider_reads",
            "connection",
            "sync",
            "can_manage",
        }
        assert row["authorization"] == "unverified"
        assert row["permissions"] == "unknown"
        assert row["provider_reads"] == "unavailable"
        assert row["can_manage"] is (role != "tenant_staff")
        assert (row["sync"] is None) == (row["platform"] != "tiktok_shop")
    seen = []
    cursor = None
    for _ in range(4):
        params: dict[str, str | int] = {"limit": 1}
        if cursor:
            params["before"] = cursor
        page = await member.get(path(**params))
        assert page.status_code == 200
        page_body = page.json()
        seen.append(page_body["items"][0]["id"])
        cursor = page_body["next_cursor"]
    assert seen == sorted(records[:4], reverse=True) and cursor is None
    filtered = await member.get(path(platform="tiktok_shop"))
    assert {row["id"] for row in filtered.json()["items"]} == {records[0], records[3]}
    bound_path = path(f"/{records[0]}", platform="tiktok_shop", shop_id=shop)
    detail = await member.get(bound_path)
    assert detail.status_code == 200 and detail.json()["id"] == records[0]
    assert "synthetic-private" not in detail.text
    for suffix, denied_params in (
        (f"/{records[0]}", {"platform": "shopee", "shop_id": shop}),
        (f"/{records[0]}", {"platform": "tiktok_shop", "shop_id": "wrong"}),
        (f"/{records[3]}", {"platform": "tiktok_shop", "shop_id": shop}),
        # Same tenant-1 record with its own shop id: only the tenant differs.
        (f"/{records[5]}", {"platform": "tiktok_shop", "shop_id": f"{shop}-other-tenant"}),
        (f"/{records[4]}", {}),
        ("", {"before": records[5]}),
        ("", {"before": records[1], "platform": "tiktok_shop"}),
        ("", {"before": records[4]}),
    ):
        assert (await member.get(path(suffix, **denied_params))).status_code == 404
    assert (await member.get(path(f"/{records[0]}", shop_id=shop))).status_code == 400
    for invalid_params in (
        {"platform": "telegram"},
        {"before": "bad"},
        {"limit": 0},
        {"limit": 51},
    ):
        assert (await member.get(path(**invalid_params))).status_code == 422
    assert (
        await member.get(path(f"/{records[0]}", platform="tiktok_shop", shop_id=""))
    ).status_code == 422
    assert (await member.get(path(client_id=w.tenants[1]))).status_code == (
        200 if role == "platform_admin" else 403
    )
    assert (await w.mutate(member, path(), {})).status_code == 405
    assert (await w.mutate(member, bound_path, {})).status_code == 405


async def seed_connection_status(w: SessionWorld, tenant: str, scenario: str) -> str:
    record_id, grant_id = str(uuid4()), str(uuid4())
    app_id = f"SYNTHETIC-{uuid4()}"
    now = datetime.now(UTC)
    platform = "shopee" if scenario == "unsupported" else "tiktok_shop"
    shop = None if scenario == "missing_shop_id" else f"SYNTHETIC-{scenario}-{uuid4()}"
    expiry = (
        None
        if scenario == "unknown_expiry"
        else now + timedelta(hours=-1 if scenario == "expired" else 2)
    )
    await w.q(
        "INSERT INTO channel_credentials (id,client_id,channel,shop_id,expires_at,"
        "access_token,refresh_token,meta) VALUES (%s,%s,%s,%s,%s,"
        "'synthetic-private-access','synthetic-private-refresh',"
        '\'{"app_secret":"synthetic-private-meta"}\')',
        record_id,
        tenant,
        platform,
        shop,
        expiry,
    )
    if scenario in {"unverified", "unsupported", "missing_shop_id", "expired"}:
        return record_id
    if scenario != "app_missing":
        await w.q("INSERT INTO ecommerce_apps VALUES (%s,1,'disabled')", app_id)
        await w.q("UPDATE ecommerce_apps SET revision=2,state='active' WHERE app_id=%s", app_id)
    scopes = (
        ["synthetic-private-scope"]
        if scenario == "permissions_missing"
        else ["seller.fs.order&fufillment.management", *PRODUCT_SCOPES]
        if scenario == "fs_scope"
        else ["seller.order.info", "seller.authorization.info", *PRODUCT_SCOPES]
        if scenario != "product_scope_missing"
        else ["seller.order.info", "seller.authorization.info"]
    )
    await w.q(
        "INSERT INTO ecommerce_grants (id,client_id,record_id,platform,shop_id,app_id,"
        "app_revision,credential_generation,revision,state,source,scopes,verified_at,expires_at) "
        "VALUES (%s,%s,%s,'tiktok_shop',%s,%s,2,%s,1,'active',"
        "'verified_authorization_exchange',%s,%s,%s)",
        grant_id,
        tenant,
        record_id,
        "OTHER-SHOP" if scenario == "wrong_shop" else shop,
        app_id,
        2 if scenario == "generation_changed" else 1,
        scopes,
        now + timedelta(hours=1) if scenario == "future_verification" else now - timedelta(hours=2),
        now - timedelta(hours=1) if scenario == "grant_expired" else now + timedelta(hours=2),
    )
    if scenario in {"revoked", "disconnected"}:
        await w.q("UPDATE ecommerce_grants SET state=%s,revision=2 WHERE id=%s", scenario, grant_id)
    if scenario == "app_disabled":
        await w.q("UPDATE ecommerce_apps SET revision=3,state='disabled' WHERE app_id=%s", app_id)
    if scenario == "app_revision":
        await w.q("UPDATE ecommerce_apps SET revision=3,state='disabled' WHERE app_id=%s", app_id)
        await w.q("UPDATE ecommerce_apps SET revision=4,state='active' WHERE app_id=%s", app_id)
    if scenario != "binding_missing":
        await w.q(
            "INSERT INTO ecommerce_shop_bindings VALUES (%s,%s,'v1:synthetic-private-cipher')",
            tenant,
            grant_id,
        )
    if scenario in {"refresh_refused", "disconnected_after_expiry"}:
        # mark_store_expired.sql: tokens cleared and expires_at set to now, so the 0320 fence
        # bumps the generation and disconnects the grant. Disconnecting on purpose wins.
        await w.q(
            "UPDATE channel_credentials SET access_token=NULL,refresh_token=NULL,"
            "expires_at=now(),disconnected_at=%s WHERE id=%s",
            now if scenario == "disconnected_after_expiry" else None,
            record_id,
        )
    if scenario == "authorization_recorded":
        await w.q(
            "INSERT INTO ecommerce_grants "
            "(id,client_id,record_id,platform,shop_id,app_id,app_revision,"
            "credential_generation,revision,state,source,scopes,verified_at,expires_at) "
            "SELECT %s,client_id,record_id,platform,shop_id,app_id,app_revision,"
            "credential_generation,2,'disconnected',source,scopes,"
            "verified_at-interval '1 hour',expires_at FROM ecommerce_grants WHERE id=%s",
            str(uuid4()),
            grant_id,
        )
    return record_id


@pytest.mark.parametrize("role", ["platform_admin", "tenant_admin", "tenant_staff"])
async def test_staff_connection_lifecycle_is_metadata_only_and_never_live(
    sessions: SessionWorld, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from csa.agents.ecommerce import connections, reader
    from csa.core import crypto, db

    w = sessions
    expected = {}
    for scenario, status in (
        ("unsupported", "unsupported"),
        ("missing_shop_id", "missing_shop_id"),
        ("unverified", "unverified"),
        ("expired", "expired"),
        ("revoked", "revoked"),
        ("disconnected", "disconnected"),
        ("generation_changed", "disconnected"),
        ("refresh_refused", "expired"),
        ("disconnected_after_expiry", "disconnected"),
        ("app_disabled", "app_unavailable"),
        ("app_missing", "app_unavailable"),
        ("app_revision", "app_unavailable"),
        ("grant_expired", "expired"),
        ("unknown_expiry", "unverified"),
        ("future_verification", "unverified"),
        ("binding_missing", "binding_missing"),
        ("permissions_missing", "permissions_missing"),
        ("product_scope_missing", "permissions_missing"),
        ("authorization_recorded", "authorization_recorded"),
        ("fs_scope", "authorization_recorded"),
        ("wrong_shop", "unverified"),
    ):
        record_id = await seed_connection_status(w, w.tenants[0], scenario)
        expected[record_id] = status
    foreign = await seed_connection_status(w, w.tenants[1], "authorization_recorded")

    def forbidden(*args, **kwargs):
        pytest.fail("metadata read must never invoke exchange, credentials crypto or provider read")

    for module, name in (
        (crypto, "decrypt"),
        (crypto, "encrypt"),
        (connections, "complete_authorization"),
        (connections, "begin_authorization"),
        (reader, "read_verified_order"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    original = db.q.fetch_all

    async def metadata_only(path, params=None, *, conn, model=None):
        if path == "business/store_connections.sql":
            sql = db.q.load(path)
            for sensitive in ("access_token", "refresh_token", "shop_cipher", "meta", "state_hash"):
                assert sensitive not in sql
        return await original(path, params, conn=conn, model=model)

    monkeypatch.setattr(db.q, "fetch_all", metadata_only)
    if role == "platform_admin":
        member = w.admin
        query = {"client_id": w.tenants[0]}
    else:
        member, _ = await w.member(role=role)
        query = {}
    base = "/console/v1/business/stores"
    response = await member.get(base, params={**query, "limit": 50})
    assert response.status_code == 200, response.text
    assert "synthetic-private" not in response.text
    items = response.json()["items"]
    assert {item["id"]: item["connection"]["status"] for item in items} == expected
    for item in items:
        status = item["connection"]["status"]
        recorded = status == "authorization_recorded"
        assert item["connection"]["live_integration"] == (
            "available" if recorded else "unavailable"
        )
        assert item["authorization"] == ("verified" if recorded else "unverified")
        assert item["permissions"] == (
            "granted" if recorded else "missing" if status == "permissions_missing" else "unknown"
        )
        assert item["provider_reads"] == ("available" if recorded else "unavailable")
        detail = await member.get(f"{base}/{item['id']}", params=query)
        assert detail.json() == item
    sample = next(
        item for item in items if item["connection"]["status"] == "authorization_recorded"
    )
    bound = await member.get(
        f"{base}/{sample['id']}",
        params={**query, "platform": "tiktok_shop", "shop_id": sample["shop_id"]},
    )
    assert bound.json() == sample
    assert (await member.get(f"{base}/{foreign}", params=query)).status_code == 404
    for action in ("connect", "reconnect"):
        assert (await w.mutate(member, f"{base}/{sample['id']}/{action}", {})).status_code == 404
    assert (await w.mutate(member, f"{base}/{sample['id']}", {})).status_code == 405


async def test_store_status_permissions_missing_without_product_scope(
    sessions: SessionWorld,
) -> None:
    w = sessions
    missing = await seed_connection_status(w, w.tenants[0], "product_scope_missing")
    granted = await seed_connection_status(w, w.tenants[0], "authorization_recorded")
    response = await w.admin.get(
        "/console/v1/business/stores", params={"client_id": w.tenants[0], "limit": 50}
    )
    assert response.status_code == 200, response.text
    statuses = {item["id"]: item["connection"]["status"] for item in response.json()["items"]}
    assert statuses == {missing: "permissions_missing", granted: "authorization_recorded"}


ORDER_STATUSES = (
    "draft",
    "pending_confirmation",
    "confirmed",
    "processing",
    "shipped",
    "delivered",
    "cancelled",
    "refunded",
)
PAYMENT_STATUSES = ("unpaid", "cod_pending", "paid", "refunded", "failed")
WINDOW_START = datetime(2026, 3, 10, 0, 0, tzinfo=UTC)
ORDER_KEYS = {
    "id",
    "status",
    "payment_status",
    "total",
    "currency",
    "channel",
    "external_order_id",
    "external_shop_id",
    "tracking_no",
    "carrier",
    "placed_at",
    "shipped_at",
    "created_at",
    "updated_at",
    "provider_status",
    "synced_at",
    "buyer_cleared",
}


async def put_order(
    w: SessionWorld,
    tenant: str,
    *,
    created_at: datetime,
    status: str = "confirmed",
    payment_status: str = "unpaid",
    channel: str = "manual",
    shop: str | None = None,
    placed_at: datetime | None = None,
    order_id: str | None = None,
) -> str:
    order_id = order_id or f"synthetic-{uuid4()}"
    await w.q(
        "INSERT INTO orders (id,client_id,status,payment_status,channel,external_shop_id,"
        "total,placed_at,created_at) VALUES (%s,%s,%s,%s,%s,%s,10.00,%s,%s)",
        order_id,
        tenant,
        status,
        payment_status,
        channel,
        shop,
        placed_at,
        created_at,
    )
    return order_id


def orders_path(tenant: str | None = None, **params: str | int) -> str:
    query = {**({"client_id": tenant} if tenant else {}), **params}
    return "/console/v1/business/orders?" + urlencode(query)


def counts_path(tenant: str | None = None, **params: str | int) -> str:
    query = {**({"client_id": tenant} if tenant else {}), **params}
    return "/console/v1/business/orders/counts?" + urlencode(query)


def expected_order(rows: dict[str, tuple[datetime, ...]], ids: list[str]) -> list[str]:
    """Order ids newest first, ties broken by id descending, as the list sorts."""
    return sorted(ids, key=lambda i: (rows[i][0], i), reverse=True)


async def test_orders_filter_by_created_window_half_open(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    start, end = WINDOW_START, WINDOW_START + timedelta(days=1)
    before = await put_order(w, tenant, created_at=start - timedelta(seconds=1))
    at_from = await put_order(w, tenant, created_at=start)
    inside = await put_order(w, tenant, created_at=start + timedelta(hours=5))
    last = await put_order(w, tenant, created_at=end - timedelta(microseconds=1))
    at_to = await put_order(w, tenant, created_at=end)
    after = await put_order(w, tenant, created_at=end + timedelta(hours=1))

    def ids(response: httpx.Response) -> set[str]:
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    window = await w.admin.get(
        orders_path(tenant, **{"from": start.isoformat(), "to": end.isoformat()})
    )
    assert ids(window) == {at_from, inside, last}, "from is inclusive, to is exclusive"
    # The same instants written with a +08:00 offset select the same rows.
    local = timedelta(hours=8)
    shifted = await w.admin.get(
        orders_path(
            tenant,
            **{
                "from": start.astimezone(timezone(local)).isoformat(),
                "to": end.astimezone(timezone(local)).isoformat(),
            },
        )
    )
    assert ids(shifted) == {at_from, inside, last}
    only_from = await w.admin.get(orders_path(tenant, **{"from": start.isoformat()}))
    assert ids(only_from) == {at_from, inside, last, at_to, after}
    only_to = await w.admin.get(orders_path(tenant, **{"to": start.isoformat()}))
    assert ids(only_to) == {before}
    # Paging a window does not leak boundary rows either way.
    paged = await w.admin.get(
        orders_path(tenant, **{"from": start.isoformat(), "to": end.isoformat(), "limit": 2})
    )
    assert [i["id"] for i in paged.json()["items"]] == [last, inside]
    assert paged.json()["next_cursor"] == inside


async def test_orders_filter_by_status_and_payment_status(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    t = WINDOW_START
    rows = {
        "shipped_paid": await put_order(
            w, tenant, created_at=t, status="shipped", payment_status="paid"
        ),
        "shipped_cod": await put_order(
            w,
            tenant,
            created_at=t + timedelta(minutes=1),
            status="shipped",
            payment_status="cod_pending",
        ),
        "draft_paid": await put_order(
            w, tenant, created_at=t + timedelta(minutes=2), status="draft", payment_status="paid"
        ),
        "refunded_failed": await put_order(
            w,
            tenant,
            created_at=t + timedelta(minutes=3),
            status="refunded",
            payment_status="failed",
        ),
    }

    async def ids(**params: str) -> set[str]:
        response = await w.admin.get(orders_path(tenant, **params))
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert await ids(status="shipped") == {rows["shipped_paid"], rows["shipped_cod"]}
    assert await ids(payment_status="paid") == {rows["shipped_paid"], rows["draft_paid"]}
    assert await ids(status="shipped", payment_status="paid") == {rows["shipped_paid"]}
    assert await ids(status="cancelled") == set()
    assert await ids(status="refunded", payment_status="failed") == {rows["refunded_failed"]}
    for status in ORDER_STATUSES:
        await ids(status=status)
    for payment_status in PAYMENT_STATUSES:
        await ids(payment_status=payment_status)
    # Combines with the existing channel and shop filters.
    store = await put_order(
        w, tenant, created_at=t, status="shipped", channel="shopee", shop="SHOP-1"
    )
    assert await ids(status="shipped", channel="shopee", shop_id="SHOP-1") == {store}
    assert await ids(status="shipped", channel="tiktok_shop") == set()


async def test_orders_filters_combine_with_cursor_paging(sessions: SessionWorld) -> None:
    w, tenant, other = sessions, sessions.tenants[0], sessions.tenants[1]
    start, end = WINDOW_START, WINDOW_START + timedelta(days=1)
    matching: dict[str, tuple[datetime, ...]] = {}
    for index in range(7):
        # Two rows share each timestamp so the id tie-breaker is exercised across pages.
        created = start + timedelta(hours=index // 2)
        order_id = await put_order(
            w, tenant, created_at=created, status="shipped", payment_status="paid"
        )
        matching[order_id] = (created,)
    # Noise: wrong status, wrong payment, outside the window, another tenant.
    await put_order(w, tenant, created_at=start, status="draft", payment_status="paid")
    await put_order(w, tenant, created_at=start, status="shipped", payment_status="unpaid")
    await put_order(w, tenant, created_at=end, status="shipped", payment_status="paid")
    await put_order(
        w, tenant, created_at=start - timedelta(seconds=1), status="shipped", payment_status="paid"
    )
    await put_order(w, other, created_at=start, status="shipped", payment_status="paid")
    params = {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "status": "shipped",
        "payment_status": "paid",
    }
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        page_params: dict[str, str | int] = {**params, "limit": 3}
        if cursor:
            page_params["before"] = cursor
        response = await w.admin.get(orders_path(tenant, **page_params))
        assert response.status_code == 200, response.text
        page = response.json()
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert cursor is None
    assert len(seen) == len(set(seen)) == 7, "no gap and no repeat"
    assert seen == expected_order(matching, list(matching))


async def test_orders_without_filters_unchanged(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    rows: dict[str, tuple[datetime, ...]] = {}
    for index, status in enumerate(("draft", "shipped", "cancelled", "shipped")):
        created = WINDOW_START + timedelta(days=index // 2)
        order_id = await put_order(w, tenant, created_at=created, status=status)
        rows[order_id] = (created,)
    response = await w.admin.get(orders_path(tenant))
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"source", "marketplace_sync", "items", "next_cursor"}
    assert [item["id"] for item in body["items"]] == expected_order(rows, list(rows))
    assert all(set(item) == ORDER_KEYS for item in body["items"]), "no customer or line items"
    assert body["next_cursor"] is None


async def test_orders_reject_unknown_status_and_inverted_range(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    await put_order(w, tenant, created_at=WINDOW_START)
    start, end = WINDOW_START, WINDOW_START + timedelta(days=1)
    for path in (orders_path, counts_path):
        response = await w.admin.get(path(tenant, payment_status="settled"))
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_payment_status")
        response = await w.admin.get(path(tenant, payment_status=""))
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_payment_status")
        inverted = await w.admin.get(
            path(tenant, **{"from": end.isoformat(), "to": start.isoformat()})
        )
        assert (inverted.status_code, inverted.json()["detail"]) == (422, "invalid_range")
        equal = await w.admin.get(
            path(tenant, **{"from": start.isoformat(), "to": start.isoformat()})
        )
        assert (equal.status_code, equal.json()["detail"]) == (422, "invalid_range")
        naive = await w.admin.get(path(tenant, **{"from": "2026-03-10T00:00:00"}))
        assert naive.status_code == 422
        garbage = await w.admin.get(path(tenant, **{"to": "yesterday"}))
        assert garbage.status_code == 422
    for status in ("sent", "", "SHIPPED", "shipped "):
        response = await w.admin.get(orders_path(tenant, status=status))
        assert (response.status_code, response.json()["detail"]) == (422, "invalid_status")
    # The existing filter errors keep their codes.
    assert (await w.admin.get(orders_path(tenant, shop_id="X"))).status_code == 400
    assert (await w.admin.get(orders_path(tenant, channel="lazada"))).status_code == 422


async def test_order_counts_ignore_the_status_filter_and_include_zeros(
    sessions: SessionWorld,
) -> None:
    w, tenant = sessions, sessions.tenants[0]
    t = WINDOW_START
    plan = [
        ("draft", "unpaid", t, "manual", None),
        ("shipped", "paid", t + timedelta(hours=1), "manual", None),
        ("shipped", "paid", t + timedelta(hours=2), "shopee", "SHOP-1"),
        ("shipped", "cod_pending", t + timedelta(hours=3), "shopee", "SHOP-1"),
        ("delivered", "paid", t + timedelta(days=2), "shopee", "SHOP-2"),
    ]
    for status, payment, created, channel, shop in plan:
        await put_order(
            w,
            tenant,
            created_at=created,
            status=status,
            payment_status=payment,
            channel=channel,
            shop=shop,
        )

    async def counts(**params: str) -> dict:
        response = await w.admin.get(counts_path(tenant, **params))
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"total", "by_status"}
        assert list(body["by_status"]) == list(ORDER_STATUSES), "every status, zeros included"
        assert body["total"] == sum(body["by_status"].values())
        return body

    everything = await counts()
    assert everything["total"] == 5
    assert everything["by_status"] == {
        **dict.fromkeys(ORDER_STATUSES, 0),
        "draft": 1,
        "shipped": 3,
        "delivered": 1,
    }
    # A status parameter is ignored so every chip shows its own number.
    assert await counts(status="shipped") == everything
    assert await counts(status="not-a-status") == everything
    window = await counts(**{"from": t.isoformat(), "to": (t + timedelta(days=1)).isoformat()})
    assert window["total"] == 4 and window["by_status"]["delivered"] == 0
    paid = await counts(payment_status="paid", status="draft")
    assert paid["total"] == 3 and paid["by_status"]["shipped"] == 2
    shop = await counts(channel="shopee", shop_id="SHOP-1")
    assert shop["total"] == 2 and shop["by_status"]["shipped"] == 2
    assert (await w.admin.get(counts_path(tenant, shop_id="SHOP-1"))).status_code == 400
    empty = await counts(channel="telegram")
    assert empty == {"total": 0, "by_status": dict.fromkeys(ORDER_STATUSES, 0)}


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff"])
async def test_order_counts_tenant_scoped(sessions: SessionWorld, role: str) -> None:
    w = sessions
    for _ in range(2):
        await put_order(w, w.tenants[0], created_at=WINDOW_START, status="shipped")
    for _ in range(3):
        await put_order(w, w.tenants[1], created_at=WINDOW_START, status="cancelled")
    member, _ = await w.member(role=role)
    own = await member.get(counts_path())
    assert own.status_code == 200, own.text
    assert own.json()["total"] == 2
    assert own.json()["by_status"]["shipped"] == 2 and own.json()["by_status"]["cancelled"] == 0
    assert (await member.get(counts_path(w.tenants[1]))).status_code == 403
    foreign = await w.admin.get(counts_path(w.tenants[1]))
    assert foreign.json()["total"] == 3 and foreign.json()["by_status"]["cancelled"] == 3
    # Same gating as the list: platform admin must pick a tenant; readers, anonymous denied.
    assert (await w.admin.get(counts_path())).status_code == 400
    reader, _ = await w.member(role="read_only")
    assert (await reader.get(counts_path())).status_code == 403
    assert (await (await w.browser()).get(counts_path())).status_code == 401
    assert (await w.mutate(member, "/console/v1/business/orders/counts", {})).status_code == 405
    # "counts" is a route, not an order id.
    assert (await member.get("/console/v1/business/orders/counts")).json().get("total") == 2


async def test_order_with_null_placed_at_is_listed(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    start, end = WINDOW_START, WINDOW_START + timedelta(days=1)
    draft = await put_order(
        w, tenant, created_at=start + timedelta(hours=1), status="draft", placed_at=None
    )
    placed = await put_order(
        w,
        tenant,
        created_at=start + timedelta(hours=2),
        status="draft",
        placed_at=start - timedelta(days=30),
    )
    window = {"from": start.isoformat(), "to": end.isoformat()}
    response = await w.admin.get(orders_path(tenant, **window))
    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert set(items) == {draft, placed}, "the window is on created_at, not placed_at"
    assert items[draft]["placed_at"] is None
    counts = await w.admin.get(counts_path(tenant, **window))
    assert counts.json()["by_status"]["draft"] == 2


# ---- TikTok order detail, listings and real store sync state (Task 16) ----

PRIVATE = "synthetic-private"
SHIP_TO = {
    "recipient_name": "Synthetic Buyer",
    "phone": "+60000000000",
    "address_text": "1 Synthetic Lane",
    "region": "MY",
}
DETAIL_KEYS = ORDER_KEYS | {"lines", "subtotal", "shipping_fee", "discount", "ship_to"}


async def put_tiktok_order(
    w: SessionWorld,
    tenant: str,
    *,
    cleared: bool = False,
    external: str | None = "SYNTH-EXT-1",
    channel: str = "tiktok_shop",
    lines: bool = True,
) -> str:
    order_id = f"tt-synthetic-{uuid4()}"
    await w.q(
        "INSERT INTO orders (id,client_id,status,payment_status,channel,external_order_id,"
        "external_shop_id,total,currency,provider_status,synced_at,provider_updated_at,"
        "ship_to,shipping_fee,discount,buyer_cleared_at,tracking_no,carrier) "
        "VALUES (%s,%s,'processing','paid',%s,%s,'SYNTH-SHOP',20.00,'MYR','AWAITING_SHIPMENT',"
        "'2026-11-01T01:00:00Z','2026-11-01T00:30:00Z',%s::jsonb,4.50,1.00,%s,"
        "'SYNTH-TRACK','Synthetic Carrier')",
        order_id,
        tenant,
        channel,
        external,
        json.dumps(SHIP_TO),
        datetime(2026, 11, 2, tzinfo=UTC) if cleared else None,
    )
    if lines:
        for sku, name, qty, price in (
            ("SKU-B", "Blue Mug", 1, "5.50"),
            ("SKU-A", "Amber Plate", 2, "3.25"),
        ):
            await w.q(
                "INSERT INTO order_items (client_id,order_id,product_id,sku,name_snapshot,"
                "unit_price,qty) VALUES (%s,%s,NULL,%s,%s,%s,%s)",
                tenant,
                order_id,
                sku,
                name,
                price,
                qty,
            )
        # The items trigger rewrites the subtotal from the lines; set TikTok's own figure.
        await w.q("UPDATE orders SET subtotal=12.00 WHERE id=%s", order_id)
    return order_id


async def put_listing(
    w: SessionWorld,
    tenant: str,
    store: str,
    *,
    name: str = "Synthetic Listing",
    variant: str | None = "Red",
    sku: str | None = None,
    image: str | None = "https://img.example.test/a.png",
    status: str = "ACTIVATE",
) -> str:
    row = await w.q(
        "INSERT INTO marketplace_listings (client_id,store_id,channel,external_product_id,"
        "external_sku_id,product_name,variant_name,seller_sku,price,currency,stock_qty,"
        "product_status,image_url,synced_at) VALUES (%s,%s,'tiktok_shop',%s,%s,%s,%s,%s,9.90,"
        "'MYR',7,%s,%s,'2026-11-01T02:00:00Z') RETURNING id",
        tenant,
        store,
        f"P-{uuid4()}",
        f"S-{uuid4()}",
        name,
        variant,
        sku,
        status,
        image,
    )
    return str(row[0]["id"])


async def put_local_product(w: SessionWorld, tenant: str, sku: str, name: str) -> str:
    product_id = str(uuid4())
    await w.q(
        "INSERT INTO products (id,client_id,sku,name,price,stock_qty,reserved_qty,attributes) "
        "VALUES (%s,%s,%s,%s,1.00,1,0,'{}')",
        product_id,
        tenant,
        sku,
        name,
    )
    return product_id


def view_for(w: SessionWorld, role: str) -> dict[str, str]:
    """Platform admin picks the tenant in the query; members use their own."""
    return {"client_id": w.tenants[0]} if role == "platform_admin" else {}


async def acting_as(w: SessionWorld, role: str) -> httpx.AsyncClient:
    return w.admin if role == "platform_admin" else (await w.member(role=role))[0]


@pytest.mark.parametrize("role", ["platform_admin", "tenant_admin", "tenant_staff"])
async def test_tiktok_order_detail_has_lines_and_ship_to_for_staff(
    sessions: SessionWorld, role: str
) -> None:
    w = sessions
    await seed_connection_status(w, w.tenants[0], "authorization_recorded")
    order_id = await put_tiktok_order(w, w.tenants[0])
    foreign = await put_tiktok_order(w, w.tenants[1])
    member, query = await acting_as(w, role), view_for(w, role)
    detail = await member.get(f"/console/v1/business/orders/{order_id}", params=query)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert set(body) == DETAIL_KEYS
    assert body["lines"] == [
        {"sku": "SKU-A", "name": "Amber Plate", "qty": 2, "unit_price": "3.25"},
        {"sku": "SKU-B", "name": "Blue Mug", "qty": 1, "unit_price": "5.50"},
    ]
    assert (body["subtotal"], body["shipping_fee"], body["discount"]) == ("12.00", "4.50", "1.00")
    assert body["ship_to"] == SHIP_TO
    assert body["buyer_cleared"] is False
    assert body["provider_status"] == "AWAITING_SHIPMENT"
    assert body["synced_at"].startswith("2026-11-01T01:00:00")
    assert body["tracking_no"] == "SYNTH-TRACK" and body["total"] == "20.00"
    listing = await member.get("/console/v1/business/orders", params=query)
    row = next(item for item in listing.json()["items"] if item["id"] == order_id)
    assert set(row) == ORDER_KEYS, "the list never carries ship-to or lines"
    assert row["provider_status"] == "AWAITING_SHIPMENT" and row["buyer_cleared"] is False
    assert (
        await member.get(f"/console/v1/business/orders/{foreign}", params=query)
    ).status_code == 404
    # The channel/shop filters of the detail still bind it to the right order.
    wrong = await member.get(
        f"/console/v1/business/orders/{order_id}", params={**query, "channel": "shopee"}
    )
    assert wrong.status_code == 404
    for response in (detail, listing):
        assert PRIVATE not in response.text and "synthetic-private-cipher" not in response.text
    assert (
        await w.mutate(member, f"/console/v1/business/orders/{order_id}", {})
    ).status_code == 405


async def test_local_order_detail_still_hides_ship_to(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    local_id, _ = await seed(w, tenant)
    # A TikTok-channel order the assistant took in a chat has no provider id: still local.
    chat_id = await put_tiktok_order(w, tenant, external=None)
    member, _ = await w.member(role="tenant_staff")
    for order_id in (local_id, chat_id):
        response = await member.get(f"/console/v1/business/orders/{order_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert "not-for-console" not in response.text and SHIP_TO["phone"] not in response.text
        assert body["ship_to"] is None and body["lines"] is None
        assert body["subtotal"] is None and body["shipping_fee"] is None
        assert body["discount"] is None
        assert body["buyer_cleared"] is False
        assert body["provider_status"] is None and body["synced_at"] is None
    page = await member.get("/console/v1/business/orders")
    assert all(set(item) == ORDER_KEYS for item in page.json()["items"])
    assert all(item["provider_status"] is None for item in page.json()["items"])


async def test_cleared_order_shows_cleared_flag_and_no_ship_to(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    # Worst case: the clearing stamp is set while ship_to still holds data; it must not leak.
    order_id = await put_tiktok_order(w, tenant, cleared=True)
    member, _ = await w.member(role="tenant_staff")
    detail = await member.get(f"/console/v1/business/orders/{order_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["buyer_cleared"] is True and body["ship_to"] is None
    assert SHIP_TO["phone"] not in detail.text and SHIP_TO["address_text"] not in detail.text
    assert len(body["lines"]) == 2 and body["total"] == "20.00"
    page = await member.get("/console/v1/business/orders")
    assert [i["buyer_cleared"] for i in page.json()["items"] if i["id"] == order_id] == [True]


def listing_path(**params: str | int) -> str:
    return "/console/v1/business/listings?" + urlencode(params)


async def test_listings_page_isolates_tenants_and_matches_same_sku_at_read_time(
    sessions: SessionWorld,
) -> None:
    w, tenant, other = sessions, sessions.tenants[0], sessions.tenants[1]
    store = await seed_connection_status(w, tenant, "authorization_recorded")
    store_two = await seed_connection_status(w, tenant, "authorization_recorded")
    foreign_store = await seed_connection_status(w, other, "authorization_recorded")
    sku = f"SKU-{uuid4()}"
    matched = await put_listing(
        w, tenant, store, name="Alpha", sku=sku, image="https://i.example/a"
    )
    no_sku = await put_listing(w, tenant, store, name="Bravo", variant=None, sku=None, image=None)
    empty_sku = await put_listing(
        w, tenant, store, name="Charlie", sku="", image="http://i.example/c"
    )
    script = await put_listing(w, tenant, store_two, name="Delta", image="javascript:alert(1)")
    foreign_listing = await put_listing(w, other, foreign_store, name="Echo", sku=sku)
    await put_local_product(w, tenant, sku, "Local Alpha")
    foreign_product = await put_local_product(w, other, f"SKU-{uuid4()}", "Foreign product")
    member, _ = await w.member(role="tenant_staff")
    response = await member.get(listing_path(limit=50))
    assert response.status_code == 200, response.text
    page = response.json()
    assert set(page) == {"items", "next_cursor"} and page["next_cursor"] is None
    items = {item["id"]: item for item in page["items"]}
    assert set(items) == {matched, no_sku, empty_sku, script}, "tenant isolation"
    assert set(items[matched]) == {
        "id",
        "product_name",
        "variant_name",
        "seller_sku",
        "price",
        "currency",
        "stock_qty",
        "product_status",
        "image_url",
        "synced_at",
        "same_sku_product",
    }
    assert items[matched]["same_sku_product"] == "Local Alpha"
    assert items[matched]["price"] == "9.90" and items[matched]["stock_qty"] == 7
    assert items[matched]["product_status"] == "ACTIVATE"
    assert items[matched]["image_url"] == "https://i.example/a"
    assert items[no_sku]["same_sku_product"] is None and items[no_sku]["variant_name"] is None
    assert items[empty_sku]["same_sku_product"] is None
    # Untrusted image URLs are only returned when they are https.
    assert items[empty_sku]["image_url"] is None and items[script]["image_url"] is None
    assert items[no_sku]["image_url"] is None
    assert "Foreign product" not in response.text and foreign_product not in response.text
    # Read time, never stored: rename the product, then delete it.
    await w.q("UPDATE products SET name='Renamed Alpha' WHERE client_id=%s AND sku=%s", tenant, sku)
    renamed = (await member.get(listing_path(limit=50))).json()["items"]
    assert [i["same_sku_product"] for i in renamed if i["id"] == matched] == ["Renamed Alpha"]
    await w.q("DELETE FROM products WHERE client_id=%s AND sku=%s", tenant, sku)
    gone = (await member.get(listing_path(limit=50))).json()["items"]
    assert [i["same_sku_product"] for i in gone if i["id"] == matched] == [None]
    columns = await w.q(
        "SELECT column_name FROM information_schema.columns WHERE table_name='marketplace_listings'"
    )
    assert "same_sku_product" not in {c["column_name"] for c in columns}
    # Filters.
    by_store = await member.get(listing_path(store_id=store_two))
    assert [i["id"] for i in by_store.json()["items"]] == [script]
    foreign_filter = await member.get(listing_path(store_id=foreign_store))
    assert foreign_filter.status_code == 200 and foreign_filter.json()["items"] == []
    by_name = await member.get(listing_path(q="alph"))
    assert [i["id"] for i in by_name.json()["items"]] == [matched]
    by_variant = await member.get(listing_path(q="RED", store_id=store_two))
    assert [i["id"] for i in by_variant.json()["items"]] == [script]
    by_sku = await member.get(listing_path(q=sku[:12].lower()))
    assert [i["id"] for i in by_sku.json()["items"]] == [matched]
    # LIKE wildcards in q are literal.
    literal = await put_listing(w, tenant, store, name="100% cotton_tee", variant="x")
    for needle, expected in (
        ("100%", {literal}),
        ("n_t", {literal}),
        ("%", {literal}),
        ("_", {literal}),
    ):
        got = await member.get(listing_path(q=needle))
        assert {i["id"] for i in got.json()["items"]} == expected, needle
    assert (await member.get(listing_path(q="x" * 101))).status_code == 422
    assert (await member.get(listing_path(q=""))).status_code == 422
    # Stable cursor paging over every row, no repeats.
    everything = {matched, no_sku, empty_sku, script, literal}
    seen: list[str] = []
    cursor = None
    for _ in range(5):
        params: dict[str, str | int] = {"limit": 1}
        if cursor:
            params["before"] = cursor
        got = await member.get(listing_path(**params))
        assert got.status_code == 200, got.text
        seen.append(got.json()["items"][0]["id"])
        cursor = got.json()["next_cursor"]
    assert set(seen) == everything and len(seen) == 5 and cursor is None
    assert (await member.get(listing_path(before=foreign_listing))).status_code == 404
    assert (await member.get(listing_path(before="bad"))).status_code == 422
    assert (await member.get(listing_path(limit=0))).status_code == 422
    assert (await member.get(listing_path(limit=51))).status_code == 422
    assert (await member.get(listing_path(client_id=other))).status_code == 403
    # Platform admin must name a tenant and then sees only that tenant.
    assert (await w.admin.get(listing_path())).status_code == 400
    admin_view = await w.admin.get(listing_path(client_id=other))
    assert [i["id"] for i in admin_view.json()["items"]] == [foreign_listing]
    for checked in (response, by_name, admin_view):
        assert PRIVATE not in checked.text and "cipher" not in checked.text


async def test_listings_need_the_package_and_a_staff_or_admin_role(
    sessions: SessionWorld,
) -> None:
    w, tenant = sessions, sessions.tenants[0]
    store = await seed_connection_status(w, tenant, "authorization_recorded")
    await put_listing(w, tenant, store)
    for role in ("tenant_admin", "tenant_staff"):
        member, _ = await w.member(role=role)
        assert (await member.get(listing_path())).status_code == 200
        assert (await w.mutate(member, "/console/v1/business/listings", {})).status_code == 405
    reader, _ = await w.member(role="read_only")
    assert (await reader.get(listing_path())).status_code == 403
    assert (await (await w.browser()).get(listing_path())).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="https://console.example.test"
    ) as machine:
        machine_headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": tenant}
        refused = await machine.get("/console/v1/business/listings", headers=machine_headers)
        assert refused.status_code == 403
    await w.q("DELETE FROM console_service_packages WHERE client_id=%s", tenant)
    staff, _ = await w.member(role="tenant_staff")
    blocked = await staff.get(listing_path())
    assert blocked.status_code == 403 and blocked.json()["detail"] == "package_inactive"


async def test_read_only_member_denied(sessions: SessionWorld) -> None:
    w, tenant = sessions, sessions.tenants[0]
    store = await seed_connection_status(w, tenant, "authorization_recorded")
    listing = await put_listing(w, tenant, store)
    order_id = await put_tiktok_order(w, tenant)
    reader, _ = await w.member(role="read_only")
    for path in (
        f"/console/v1/business/orders/{order_id}",
        "/console/v1/business/orders",
        "/console/v1/business/listings",
        f"/console/v1/business/listings?store_id={store}",
        f"/console/v1/business/listings?before={listing}",
        "/console/v1/business/stores",
    ):
        response = await reader.get(path)
        assert response.status_code == 403, path
        assert SHIP_TO["phone"] not in response.text and "Synthetic Listing" not in response.text


async def test_store_page_reports_sync_times_and_failing_problem(sessions: SessionWorld) -> None:
    w, tenant, other = sessions, sessions.tenants[0], sessions.tenants[1]
    healthy = await seed_connection_status(w, tenant, "authorization_recorded")
    failing = await seed_connection_status(w, tenant, "authorization_recorded")
    never = await seed_connection_status(w, tenant, "authorization_recorded")
    pending = await seed_connection_status(w, tenant, "missing_shop_id")
    foreign = await seed_connection_status(w, other, "authorization_recorded")
    shopee = await seed_connection_status(w, tenant, "unsupported")
    for store, orders_at, products_at, notified, error in (
        (healthy, "2026-11-03T10:00:00Z", "2026-11-03T11:00:00Z", None, None),
        (failing, "2026-11-02T10:00:00Z", None, "2026-11-03T12:00:00Z", "synthetic-private-error"),
        (foreign, "2026-11-03T10:00:00Z", "2026-11-03T11:00:00Z", "2026-11-03T12:00:00Z", None),
    ):
        await w.q(
            "INSERT INTO marketplace_store_state (store_id,client_id,orders_synced_at,"
            "products_synced_at,last_error_code,problem_notified_at) "
            "SELECT id,client_id,%s,%s,%s,%s FROM channel_credentials WHERE id=%s",
            orders_at,
            products_at,
            error,
            notified,
            store,
        )
    # A row with last_error_code but no notice is not yet "sync failing".
    await w.q(
        "INSERT INTO marketplace_store_state (store_id,client_id,last_error_code) "
        "SELECT id,client_id,'sync_failed' FROM channel_credentials WHERE id=%s",
        never,
    )
    results = {}
    for role in ("tenant_admin", "tenant_staff"):
        member, _ = await w.member(role=role)
        response = await member.get("/console/v1/business/stores", params={"limit": 50})
        assert response.status_code == 200, response.text
        assert "synthetic-private" not in response.text and "sync_failed" not in response.text
        items = {item["id"]: item for item in response.json()["items"]}
        assert foreign not in items
        assert all(item["can_manage"] is (role == "tenant_admin") for item in items.values())
        results[role] = items
        detail = await member.get(f"/console/v1/business/stores/{failing}")
        assert detail.json() == items[failing]
        bound = await member.get(
            f"/console/v1/business/stores/{healthy}",
            params={"platform": "tiktok_shop", "shop_id": items[healthy]["shop_id"]},
        )
        assert bound.json() == items[healthy]
    items = results["tenant_admin"]

    def times(item: dict) -> tuple[datetime | None, datetime | None, str | None]:
        sync = item["sync"]
        parse = lambda v: datetime.fromisoformat(v) if v else None  # noqa: E731
        return parse(sync["orders_synced_at"]), parse(sync["products_synced_at"]), sync["problem"]

    t = lambda s: datetime.fromisoformat(s)  # noqa: E731
    assert times(items[healthy]) == (
        t("2026-11-03T10:00:00+00:00"),
        t("2026-11-03T11:00:00+00:00"),
        None,
    )
    assert times(items[failing]) == (t("2026-11-02T10:00:00+00:00"), None, "sync_failing")
    assert times(items[never]) == (None, None, None)
    assert times(items[pending]) == (None, None, None)
    assert items[shopee]["sync"] is None
    assert set(items[healthy]["sync"]) == {"orders_synced_at", "products_synced_at", "problem"}
    # Platform admin can manage any tenant's stores.
    admin = await w.admin.get(
        "/console/v1/business/stores", params={"client_id": tenant, "limit": 50}
    )
    assert all(item["can_manage"] is True for item in admin.json()["items"])
    recorded = items[healthy]
    assert recorded["connection"] == {
        "status": "authorization_recorded",
        "live_integration": "available",
    }
    assert (recorded["authorization"], recorded["permissions"], recorded["provider_reads"]) == (
        "verified",
        "granted",
        "available",
    )


async def test_marketplace_sync_reflects_a_connected_tiktok_store(sessions: SessionWorld) -> None:
    w, tenant, other = sessions, sessions.tenants[0], sessions.tenants[1]
    for scenario in ("missing_shop_id", "expired", "disconnected", "revoked", "refresh_refused"):
        await seed_connection_status(w, tenant, scenario)
    await seed_connection_status(w, other, "authorization_recorded")

    async def states(client_id: str) -> set[str]:
        found = set()
        for resource in ("orders", "products", "stores"):
            response = await w.admin.get(
                f"/console/v1/business/{resource}", params={"client_id": client_id}
            )
            assert response.status_code == 200, response.text
            found.add(response.json()["marketplace_sync"])
        return found

    assert await states(tenant) == {"not_connected"}
    assert await states(other) == {"connected"}
    await seed_connection_status(w, tenant, "authorization_recorded")
    assert await states(tenant) == {"connected"}
    # A disconnect (the grant is revoked through the fence) ends it again.
    await w.q("UPDATE channel_credentials SET disconnected_at=now() WHERE client_id=%s", tenant)
    assert await states(tenant) == {"not_connected"}
