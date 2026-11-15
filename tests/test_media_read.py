"""The picture read route: each kind follows its owner's read rule; anything else is a plain 404."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from csa.core.tenancy import tx
from csa.media import service
from csa.media.api import build_media_router
from csa.media.images import check_image

from tests.conftest import requires_docker
from tests.media_images import jpeg, png
from tests.test_console_services import activate
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/media"
NOT_FOUND = {"detail": "picture_not_found"}


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_media_router())
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "subscriptions", "orders_products")


async def seed(
    w: SessionWorld, tenant: str, kind: Any, subject: str, data: bytes | None = None
) -> uuid.UUID:
    data = data or png(800, 800)
    async with tx(w.pool, tenant) as conn:
        stored = await service.put(
            conn,
            uuid.UUID(tenant),
            kind,
            subject,
            data,
            check_image(data, None),
            source="upload",
            actor_id=None,
        )
    assert stored is not None
    return stored.id


async def set_package(w: SessionWorld, tenant: str, package: str, status: str) -> None:
    await w.q(
        "UPDATE console_service_packages SET status=%s WHERE client_id=%s AND package=%s",
        status,
        tenant,
        package,
    )


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
@pytest.mark.parametrize("kind", ["payment_qr", "plan", "product"])
async def test_each_kind_follows_its_owners_read_rule(
    sessions: SessionWorld, role: str, kind: str
) -> None:
    w = sessions
    asset = await seed(w, w.tenants[0], kind, "subject-1")
    member, _ = await w.member(role=role)
    shown = await member.get(f"{BASE}/{asset}")
    if role == "read_only" and kind in ("payment_qr", "product"):
        assert shown.status_code == 404 and shown.json() == NOT_FOUND
    else:
        assert shown.status_code == 200, shown.text
        assert shown.content == png(800, 800)


async def test_package_off_hides_the_picture(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    qr = await seed(w, tenant, "payment_qr", "m1")
    plan = await seed(w, tenant, "plan", "gold")
    product = await seed(w, tenant, "product", "p1")
    staff, _ = await w.member(role="tenant_staff")
    for asset in (qr, plan, product):
        assert (await staff.get(f"{BASE}/{asset}")).status_code == 200

    await set_package(w, tenant, "subscriptions", "suspended")
    for asset in (qr, plan):
        hidden = await staff.get(f"{BASE}/{asset}")
        assert hidden.status_code == 404 and hidden.json() == NOT_FOUND
    assert (await staff.get(f"{BASE}/{product}")).status_code == 200

    await set_package(w, tenant, "orders_products", "suspended")
    hidden = await staff.get(f"{BASE}/{product}")
    assert hidden.status_code == 404 and hidden.json() == NOT_FOUND

    # One of the two product packages is enough.
    await w.q(
        "INSERT INTO console_service_packages (client_id, package, status, activated_at)"
        " VALUES (%s, 'marketplaces', 'active', now())",
        tenant,
    )
    assert (await staff.get(f"{BASE}/{product}")).status_code == 200
    await set_package(w, tenant, "subscriptions", "active")
    assert (await staff.get(f"{BASE}/{plan}")).status_code == 200


async def test_unknown_and_foreign_ids_are_404(sessions: SessionWorld) -> None:
    w = sessions
    foreign = await seed(w, w.tenants[1], "plan", "gold")
    mine = await seed(w, w.tenants[0], "plan", "gold")
    staff, _ = await w.member(role="tenant_staff")
    assert (await staff.get(f"{BASE}/{mine}")).status_code == 200
    unknown = await staff.get(f"{BASE}/{uuid.uuid4()}")
    other = await staff.get(f"{BASE}/{foreign}")
    assert unknown.status_code == other.status_code == 404
    assert unknown.json() == other.json() == NOT_FOUND
    assert unknown.headers.get("etag") is None and other.headers.get("etag") is None
    malformed = await staff.get(f"{BASE}/not-a-uuid")
    assert malformed.status_code == 422


async def test_training_thumbnail_is_never_served_here(sessions: SessionWorld) -> None:
    w = sessions
    asset = await seed(w, w.tenants[0], "training_thumbnail", "item-1", jpeg(320, 180))
    admin, _ = await w.member(role="tenant_admin")
    hidden = await admin.get(f"{BASE}/{asset}")
    assert hidden.status_code == 404 and hidden.json() == NOT_FOUND
    platform = await w.admin.get(f"{BASE}/{asset}", params={"client_id": w.tenants[0]})
    assert platform.status_code == 404 and platform.json() == NOT_FOUND


async def test_headers_and_revalidation(sessions: SessionWorld) -> None:
    w = sessions
    tenant = w.tenants[0]
    first_bytes = png(800, 800)
    asset = await seed(w, tenant, "plan", "gold", first_bytes)
    staff, _ = await w.member(role="tenant_staff")
    shown = await staff.get(f"{BASE}/{asset}")
    assert shown.status_code == 200 and shown.content == first_bytes
    etag = f'"{check_image(first_bytes, None).sha256}"'
    assert shown.headers["content-type"] == "image/png"
    assert shown.headers["etag"] == etag
    assert shown.headers["cache-control"] == "private, no-cache"
    assert shown.headers["x-content-type-options"] == "nosniff"
    assert shown.headers["content-security-policy"] == "default-src 'none'; sandbox"

    same = await staff.get(f"{BASE}/{asset}", headers={"If-None-Match": etag})
    assert same.status_code == 304 and same.content == b""
    assert same.headers["etag"] == etag and same.headers["cache-control"] == "private, no-cache"
    weak = await staff.get(f"{BASE}/{asset}", headers={"If-None-Match": f'"other", W/{etag}'})
    assert weak.status_code == 304

    second_bytes = jpeg(640, 480)
    assert await seed(w, tenant, "plan", "gold", second_bytes) == asset
    changed = await staff.get(f"{BASE}/{asset}", headers={"If-None-Match": etag})
    assert changed.status_code == 200 and changed.content == second_bytes
    assert changed.headers["content-type"] == "image/jpeg"
    assert changed.headers["etag"] == f'"{check_image(second_bytes, None).sha256}"'

    # A cached copy is never revalidated into a picture the viewer may no longer read.
    await set_package(w, tenant, "subscriptions", "suspended")
    gone = await staff.get(f"{BASE}/{asset}", headers={"If-None-Match": changed.headers["etag"]})
    assert gone.status_code == 404


async def test_platform_admin_reads_with_client_id(sessions: SessionWorld) -> None:
    w = sessions
    asset = await seed(w, w.tenants[0], "payment_qr", "m1")
    bare = await w.admin.get(f"{BASE}/{asset}")
    assert bare.status_code == 400
    shown = await w.admin.get(f"{BASE}/{asset}", params={"client_id": w.tenants[0]})
    assert shown.status_code == 200 and shown.content == png(800, 800)
    wrong = await w.admin.get(f"{BASE}/{asset}", params={"client_id": w.tenants[1]})
    assert wrong.status_code == 404 and wrong.json() == NOT_FOUND


async def test_machine_token_is_refused(sessions: SessionWorld) -> None:
    w = sessions
    asset = await seed(w, w.tenants[0], "plan", "gold")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=w.app), base_url=ORIGIN
    ) as machine:
        refused = await machine.get(
            f"{BASE}/{asset}",
            headers={"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": w.tenants[0]},
        )
    assert refused.status_code == 403


async def test_refusals_and_revalidation_never_read_the_bytes(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = sessions
    tenant = w.tenants[0]
    data = png(800, 800)
    qr = await seed(w, tenant, "payment_qr", "m1", data)
    plan = await seed(w, tenant, "plan", "gold", data)
    reads: list[uuid.UUID] = []
    original = service.get

    async def counted(conn: Any, client_id: uuid.UUID, asset_id: uuid.UUID) -> Any:
        reads.append(asset_id)
        return await original(conn, client_id, asset_id)

    monkeypatch.setattr(service, "get", counted)
    reader, _ = await w.member(role="read_only")
    staff, _ = await w.member(role="tenant_staff")
    etag = f'"{check_image(data, None).sha256}"'

    assert (await reader.get(f"{BASE}/{qr}")).status_code == 404  # role refused
    assert (await staff.get(f"{BASE}/{uuid.uuid4()}")).status_code == 404  # unknown
    same = await staff.get(f"{BASE}/{plan}", headers={"If-None-Match": etag})
    assert same.status_code == 304
    await set_package(w, tenant, "subscriptions", "suspended")
    assert (await staff.get(f"{BASE}/{plan}")).status_code == 404  # package off
    assert reads == []

    await set_package(w, tenant, "subscriptions", "active")
    shown = await staff.get(f"{BASE}/{plan}")
    assert shown.status_code == 200 and shown.content == data
    assert reads == [plan]
