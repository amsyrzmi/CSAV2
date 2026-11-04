"""M6 local read models on disposable synthetic tenants; never call marketplaces."""

from datetime import UTC, datetime, timedelta, timezone
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest

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
        assert body["marketplace_sync"] == "unavailable"
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
        assert page["source"] == "local_records" and page["marketplace_sync"] == "unavailable"
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
    shop = "SYNTH/shop+?&=%"
    records = []
    for tenant, platform, shop_id, expiry in (
        (w.tenants[0], "tiktok_shop", shop, "2100-01-01T00:00:00Z"),
        (w.tenants[0], "shopee", shop, "2000-01-01T00:00:00Z"),
        (w.tenants[0], "lazada", None, None),
        (w.tenants[0], "tiktok_shop", None, None),
        (w.tenants[0], "telegram", shop, None),
        (w.tenants[1], "tiktok_shop", shop, None),
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
    assert body["marketplace_sync"] == "unavailable"
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
        }
        assert row["authorization"] == "unverified"
        assert row["permissions"] == "unknown"
        assert row["provider_reads"] == "unavailable"
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
        (f"/{records[5]}", {"platform": "tiktok_shop", "shop_id": shop}),
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
    shop = None if scenario == "missing_shop_id" else f"SYNTHETIC-{scenario}"
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
        else ["seller.fs.order&fufillment.management"]
        if scenario == "fs_scope"
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
    from csa.channels.marketplace import oauth
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
        ("app_disabled", "app_unavailable"),
        ("app_missing", "app_unavailable"),
        ("app_revision", "app_unavailable"),
        ("grant_expired", "expired"),
        ("unknown_expiry", "unverified"),
        ("future_verification", "unverified"),
        ("binding_missing", "binding_missing"),
        ("permissions_missing", "permissions_missing"),
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
        (oauth, "exchange_tiktok_shop_token"),
        (oauth, "save_tiktok_shop_credentials"),
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
        assert item["connection"]["live_integration"] == "unavailable"
        assert item["authorization"] == "unverified"
        assert item["permissions"] == "unknown" and item["provider_reads"] == "unavailable"
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
