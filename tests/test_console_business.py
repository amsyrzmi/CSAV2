"""M6 local read models on disposable synthetic tenants; never call marketplaces."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

import httpx
import pytest

from tests.conftest import requires_docker
from tests.test_console_sessions import MACHINE_TOKEN, SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]


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
