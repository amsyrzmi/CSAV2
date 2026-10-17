"""M6 local read models on disposable synthetic tenants; never call marketplaces."""

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
    for suffix in ("orders", f"orders/{order_id}", "products", f"products/{product_id}"):
        path = f"/console/v1/business/{suffix}"
        assert (await reader.get(path)).status_code == 403
        assert (await anonymous.get(path)).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url="https://console.example.test"
    ) as machine:
        for suffix in ("orders", f"orders/{order_id}", "products", f"products/{product_id}"):
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
