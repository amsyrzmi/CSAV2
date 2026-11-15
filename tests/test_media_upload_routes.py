"""Picture upload and remove routes: QR payment methods, plans and products.

Each route mirrors its owner's write rule, refuses a role before the body is read, needs CSRF, and
answers the picture check's refusals as plain codes. Images are built at run time.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from csa.media.api import build_media_router
from csa.payments.api import build_payments_router
from csa.subscriptions.api import build_subscriptions_router

from tests.conftest import requires_docker
from tests.media_images import jpeg, png
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

V1 = "/console/v1"
QR_DETAILS = {"duitnow_id": "SYNTH-0001", "account_name": "Kedai Contoh Sdn Bhd"}
TRANSFER_DETAILS = {
    "bank_name": "Synthetic Bank",
    "account_name": "Kedai Contoh Sdn Bhd",
    "account_number": "512000000001",
}


@pytest.fixture(autouse=True)
async def routers(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_payments_router())
    sessions.app.include_router(build_subscriptions_router())
    sessions.app.include_router(build_media_router())
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "subscriptions", "orders_products")


async def put(
    w: SessionWorld,
    client: httpx.AsyncClient,
    path: str,
    data: bytes,
    name: str = "picture.png",
    *,
    csrf: bool = True,
) -> httpx.Response:
    media_type = "image/jpeg" if name.endswith(".jpg") else "image/png"
    headers = w.headers(client) if csrf else {"Origin": w.headers(client)["Origin"]}
    return await client.put(path, files={"file": (name, data, media_type)}, headers=headers)


async def remove(
    w: SessionWorld, client: httpx.AsyncClient, path: str, *, csrf: bool = True
) -> httpx.Response:
    headers = w.headers(client) if csrf else {"Origin": w.headers(client)["Origin"]}
    return await client.delete(path, headers=headers)


async def qr_method(w: SessionWorld, admin: httpx.AsyncClient, kind: str = "qr") -> dict[str, Any]:
    details = QR_DETAILS if kind == "qr" else TRANSFER_DETAILS
    made = await w.mutate(
        admin, f"{V1}/payments/methods", {"kind": kind, "label": "DuitNow", "details": details}
    )
    assert made.status_code == 201, made.text
    return made.json()


async def seed_plan(w: SessionWorld, tenant: str, code: str, *, active: bool = True) -> None:
    await w.q(
        "INSERT INTO plans (client_id, code, name, price, billing_interval, is_active) "
        "VALUES (%s, %s, 'Synthetic Plan', 59.90, 'month', %s)",
        tenant,
        code,
        active,
    )


async def seed_product(w: SessionWorld, tenant: str) -> str:
    product_id = str(uuid.uuid4())
    await w.q(
        "INSERT INTO products (id,client_id,sku,name,price,stock_qty,reserved_qty,attributes) "
        "VALUES (%s,%s,%s,'Synthetic Product',12.34,9,2,'{}')",
        product_id,
        tenant,
        f"SYNTH-{product_id}",
    )
    return product_id


async def audit_rows(w: SessionWorld, tenant: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT action, target_id FROM console_audit WHERE client_id=%s"
        " AND action LIKE 'media.%%' ORDER BY created_at, action",
        tenant,
    )


async def test_qr_picture_set_replace_remove(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    admin, _ = await w.member(role="tenant_admin")
    method = await qr_method(w, admin)
    path = f"{V1}/payments/methods/{method['id']}/picture"
    assert method["picture"] is None

    first = await put(w, admin, path, png(800, 800), "qr.png")
    assert first.status_code == 200, first.text
    assert set(first.json()) == {"id", "version"}
    listed = await admin.get(f"{V1}/payments/methods")
    assert [m["picture"] for m in listed.json()] == [first.json()]
    shown = await admin.get(f"{V1}/media/{first.json()['id']}")
    assert shown.status_code == 200 and shown.content == png(800, 800)

    second = await put(w, admin, path, jpeg(900, 900), "qr.jpg")
    assert second.status_code == 200, second.text
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["version"] != first.json()["version"]
    shown = await admin.get(f"{V1}/media/{first.json()['id']}")
    assert shown.content == jpeg(900, 900) and shown.headers["content-type"] == "image/jpeg"

    gone = await remove(w, admin, path)
    assert gone.status_code == 204 and gone.content == b""
    listed = await admin.get(f"{V1}/payments/methods")
    assert [m["picture"] for m in listed.json()] == [None]
    again = await remove(w, admin, path)
    assert again.status_code == 204

    target = f"payment_qr:{method['id']}"
    assert await audit_rows(w, tenant) == [
        {"action": "media.picture.set", "target_id": target},
        {"action": "media.picture.set", "target_id": target},
        {"action": "media.picture.removed", "target_id": target},
    ]


async def test_qr_route_refuses_a_non_qr_method(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    method = await qr_method(w, admin, kind="transfer")
    path = f"{V1}/payments/methods/{method['id']}/picture"
    refused = await put(w, admin, path, png(800, 800))
    assert refused.status_code == 422 and refused.json() == {"detail": "picture_kind_not_qr"}
    removed = await remove(w, admin, path)
    assert removed.status_code == 422 and removed.json() == {"detail": "picture_kind_not_qr"}
    assert await w.q("SELECT id FROM media_assets WHERE client_id=%s", w.tenants[0]) == []


def multipart(data: bytes) -> tuple[list[bool], AsyncIterator[bytes], str]:
    """A multipart body as a stream that records whether the server read any of it."""
    boundary = "synthetic-boundary"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="qr.png"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    read: list[bool] = []

    async def stream() -> AsyncIterator[bytes]:
        read.append(True)
        yield body

    return read, stream(), f"multipart/form-data; boundary={boundary}"


async def test_payment_picture_is_admin_only(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    method = await qr_method(w, admin)
    path = f"{V1}/payments/methods/{method['id']}/picture"
    for role in ("tenant_staff", "read_only"):
        member, _ = await w.member(role=role)
        read, body, content_type = multipart(png(800, 800))
        refused = await member.put(
            path, content=body, headers={**w.headers(member), "Content-Type": content_type}
        )
        assert refused.status_code == 403, (role, refused.text)
        assert read == [], role  # refused before the body was read
        assert (await remove(w, member, path)).status_code == 403
    assert await w.q("SELECT id FROM media_assets WHERE client_id=%s", w.tenants[0]) == []


async def test_plan_picture_writers(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    await seed_plan(w, tenant, "gold")
    await seed_plan(w, tenant, "old", active=False)
    staff, _ = await w.member(role="tenant_staff")
    for code in ("gold", "old"):
        stored = await put(w, staff, f"{V1}/subscriptions/plans/{code}/picture", png(800, 600))
        assert stored.status_code == 200, stored.text
    plans = await staff.get(f"{V1}/subscriptions/plans", params={"include_archived": "true"})
    pictures = {p["code"]: p["picture"] for p in plans.json()}
    assert pictures["gold"] is not None and pictures["old"] is not None
    gone = await remove(w, staff, f"{V1}/subscriptions/plans/old/picture")
    assert gone.status_code == 204
    actions = [row["target_id"] for row in await audit_rows(w, tenant)]
    assert actions == ["plan:gold", "plan:old", "plan:old"]


async def test_product_picture_writers(sessions: SessionWorld) -> None:
    w = sessions
    product_id = await seed_product(w, w.tenants[0])
    staff, _ = await w.member(role="tenant_staff")
    path = f"{V1}/business/products/{product_id}/picture"
    stored = await put(w, staff, path, jpeg(1200, 800), "shoe.jpg")
    assert stored.status_code == 200, stored.text
    detail = await staff.get(f"{V1}/business/products/{product_id}")
    assert detail.json()["picture"] == stored.json()
    page = await staff.get(f"{V1}/business/products")
    assert [item["picture"] for item in page.json()["items"]] == [stored.json()]
    assert (await remove(w, staff, path)).status_code == 204
    assert (await staff.get(f"{V1}/business/products/{product_id}")).json()["picture"] is None


async def test_read_only_never_uploads(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    admin, _ = await w.member(role="tenant_admin")
    method = await qr_method(w, admin)
    await seed_plan(w, tenant, "gold")
    product_id = await seed_product(w, tenant)
    reader, _ = await w.member(role="read_only")
    for path in (
        f"{V1}/payments/methods/{method['id']}/picture",
        f"{V1}/subscriptions/plans/gold/picture",
        f"{V1}/business/products/{product_id}/picture",
    ):
        read, body, content_type = multipart(png(800, 800))
        refused = await reader.put(
            path, content=body, headers={**w.headers(reader), "Content-Type": content_type}
        )
        assert refused.status_code == 403, (path, refused.text)
        assert read == [], path
        assert (await remove(w, reader, path)).status_code == 403, path
    assert await w.q("SELECT id FROM media_assets WHERE client_id=%s", tenant) == []


@pytest.mark.parametrize(
    ("data", "name", "status", "code"),
    [
        (png(800, 800), "qr.jpg", 422, "picture_type_unsupported"),
        (
            b"<svg xmlns='http://www.w3.org/2000/svg'></svg>",
            "qr.svg",
            422,
            "picture_type_unsupported",
        ),
        (b"", "qr.png", 422, "picture_empty"),
        (png(5000, 1000), "qr.png", 422, "picture_dimensions"),
        (png(800, 800, trailing=b"PK\x03\x04 hidden archive"), "qr.png", 422, "picture_unreadable"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024), "qr.png", 413, None),
    ],
    ids=["renamed_png", "svg", "zero_bytes", "5000px", "polyglot", "3mb"],
)
async def test_upload_refusals_are_plain_codes(
    sessions: SessionWorld, data: bytes, name: str, status: int, code: str | None
) -> None:
    w = sessions
    await seed_plan(w, w.tenants[0], "gold")
    staff, _ = await w.member(role="tenant_staff")
    refused = await put(w, staff, f"{V1}/subscriptions/plans/gold/picture", data, name)
    assert refused.status_code == status, refused.text
    if code is not None:
        assert refused.json() == {"detail": code}
    assert await w.q("SELECT id FROM media_assets WHERE client_id=%s", w.tenants[0]) == []
    assert await audit_rows(w, w.tenants[0]) == []


async def test_picture_limit_is_409(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    await w.q(
        "INSERT INTO media_assets (client_id, kind, subject_key, media_type, byte_size, width,"
        " height, sha256, source) SELECT %s, 'product', 'p' || n, 'image/png', 1000, 800, 800,"
        " repeat('a', 64), 'upload' FROM generate_series(1, 300) AS n",
        tenant,
    )
    await seed_plan(w, tenant, "gold")
    staff, _ = await w.member(role="tenant_staff")
    refused = await put(w, staff, f"{V1}/subscriptions/plans/gold/picture", png(800, 800))
    assert refused.status_code == 409 and refused.json() == {"detail": "picture_limit"}


async def test_unknown_subject_is_404(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    other_admin, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    foreign_method = await qr_method(w, other_admin)
    await seed_plan(w, w.tenants[1], "gold")
    foreign_product = await seed_product(w, w.tenants[1])
    for path, detail in (
        (f"{V1}/payments/methods/{uuid.uuid4()}/picture", "method_not_found"),
        (f"{V1}/payments/methods/{foreign_method['id']}/picture", "method_not_found"),
        (f"{V1}/subscriptions/plans/gold/picture", "plan_not_found"),
        (f"{V1}/business/products/{uuid.uuid4()}/picture", "product not found"),
        (f"{V1}/business/products/{foreign_product}/picture", "product not found"),
    ):
        stored = await put(w, admin, path, png(800, 800))
        assert stored.status_code == 404 and stored.json() == {"detail": detail}, path
        removed = await remove(w, admin, path)
        assert removed.status_code == 404 and removed.json() == {"detail": detail}, path
    for tenant in w.tenants:
        assert await w.q("SELECT id FROM media_assets WHERE client_id=%s", tenant) == []


async def test_writes_need_csrf(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    admin, _ = await w.member(role="tenant_admin")
    method = await qr_method(w, admin)
    await seed_plan(w, tenant, "gold")
    product_id = await seed_product(w, tenant)
    for path in (
        f"{V1}/payments/methods/{method['id']}/picture",
        f"{V1}/subscriptions/plans/gold/picture",
        f"{V1}/business/products/{product_id}/picture",
    ):
        assert (await put(w, admin, path, png(800, 800), csrf=False)).status_code == 403, path
        assert (await remove(w, admin, path, csrf=False)).status_code == 403, path
    assert await w.q("SELECT id FROM media_assets WHERE client_id=%s", tenant) == []


async def test_two_files_are_refused(sessions: SessionWorld) -> None:
    w = sessions
    await seed_plan(w, w.tenants[0], "gold")
    staff, _ = await w.member(role="tenant_staff")
    refused = await staff.put(
        f"{V1}/subscriptions/plans/gold/picture",
        files=[
            ("file", ("a.png", png(800, 800), "image/png")),
            ("file", ("b.png", png(800, 800), "image/png")),
        ],
        headers=w.headers(staff),
    )
    assert refused.status_code == 422, refused.text
    assert await w.q("SELECT id FROM media_assets WHERE client_id=%s", w.tenants[0]) == []


async def test_patch_keeps_the_picture(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    admin, _ = await w.member(role="tenant_admin")
    method = await qr_method(w, admin)
    await seed_plan(w, tenant, "gold")
    qr = await put(w, admin, f"{V1}/payments/methods/{method['id']}/picture", png(800, 800))
    plan = await put(w, admin, f"{V1}/subscriptions/plans/gold/picture", png(640, 640))
    assert qr.status_code == plan.status_code == 200

    patched_plan = await w.mutate(
        admin, f"{V1}/subscriptions/plans/gold", {"name": "Gold Plus"}, method="PATCH"
    )
    assert patched_plan.status_code == 200, patched_plan.text
    assert patched_plan.json()["picture"] == plan.json()
    patched_method = await w.mutate(
        admin,
        f"{V1}/payments/methods/{method['id']}",
        {"label": "DuitNow Main", "version": method["version"]},
        method="PATCH",
    )
    assert patched_method.status_code == 200, patched_method.text
    assert patched_method.json()["picture"] == qr.json()
