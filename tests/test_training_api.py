"""Training library routes: roles, uploads, storage, removal and cross-company isolation."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.storage.base import ObjectStore, StorageError, StorageNotFound
from csa.training import limits
from csa.training.limits import MAX_FILE_BYTES
from csa.training.service import cleanup
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import MACHINE_TOKEN, ORIGIN, SessionWorld
from tests.test_training_pdf import PORTRAIT, WIDESCREEN, bad_files, make_pdf

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

ITEMS = "/console/v1/training/items"
ITEM_FIELDS = {
    "id",
    "title",
    "description",
    "category",
    "view_mode",
    "page_count",
    "byte_size",
    "created_at",
    "uploaded_by_name",
    "has_summary",
}


async def enable(world: SessionWorld, tenant: str | None = None) -> str:
    tenant = tenant or world.tenants[0]
    await activate(world, tenant, "training_library")
    return tenant


async def upload(
    world: SessionWorld,
    browser: httpx.AsyncClient,
    data: bytes,
    name: str = "deck.pdf",
    *,
    key: str | None = None,
    query: str = "",
) -> httpx.Response:
    return await browser.post(
        ITEMS + query,
        files={"file": (name, data, "application/pdf")},
        headers={**world.headers(browser), "Idempotency-Key": key or str(uuid4())},
    )


async def patch(
    world: SessionWorld, browser: httpx.AsyncClient, item: str, body: Any, query: str = ""
) -> httpx.Response:
    return await world.mutate(browser, f"{ITEMS}/{item}{query}", body, method="PATCH")


async def remove(
    world: SessionWorld, browser: httpx.AsyncClient, item: str, query: str = ""
) -> httpx.Response:
    return await world.mutate(browser, f"{ITEMS}/{item}{query}", method="DELETE")


async def make_owner(world: SessionWorld, tenant: str, user_id: str) -> None:
    await world.q(
        "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
        tenant,
        user_id,
        world.admin_id,
    )


async def rows(world: SessionWorld, tenant: str) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT * FROM training_items WHERE client_id=%s ORDER BY created_at", tenant
    )


async def audits(world: SessionWorld, tenant: str, action: str) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT actor_id, target_id, details FROM console_audit"
        " WHERE client_id=%s AND action=%s ORDER BY created_at",
        tenant,
        action,
    )


def names(store: ObjectStore, tenant: str) -> list[str]:
    return store.list_names(UUID(tenant))


def detail(response: httpx.Response) -> Any:
    return response.json().get("detail")


async def test_every_route_needs_the_package(sessions: SessionWorld) -> None:
    tenant = sessions.tenants[0]
    q = f"?client_id={tenant}"
    staff, _ = await sessions.member()
    item = str(uuid4())
    for browser, query in ((sessions.admin, q), (staff, "")):
        answers = [
            await browser.get(ITEMS + query),
            await upload(sessions, browser, make_pdf(), query=query),
            await patch(sessions, browser, item, {"title": "Synthetic"}, query),
            await remove(sessions, browser, item, query),
            await browser.get(f"{ITEMS}/{item}/file{query}"),
        ]
        for answer in answers:
            assert answer.status_code == 403, answer.text
            assert detail(answer) == "package_inactive"
    await enable(sessions, tenant)
    assert (await staff.get(ITEMS)).status_code == 200


async def test_roles_matrix(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    other = await enable(sessions, sessions.tenants[1])
    q = f"?client_id={tenant}"
    admin, _ = await sessions.member(role="tenant_admin")
    owner, owner_me = await sessions.member()
    await make_owner(sessions, tenant, owner_me["user"]["id"])
    staff, _ = await sessions.member()
    reader, _ = await sessions.member(role="read_only")
    foreign_admin, _ = await sessions.member(role="tenant_admin", tenant=other)

    # Platform admin acting on the company, tenant admin and the HR owner (a staff member) manage.
    for browser, query in ((sessions.admin, q), (admin, ""), (owner, "")):
        created = await upload(sessions, browser, make_pdf(), query=query)
        assert created.status_code == 201, created.text
        item = created.json()["id"]
        edited = await patch(sessions, browser, item, {"title": "Renamed"}, query)
        assert edited.status_code == 200 and edited.json()["title"] == "Renamed", edited.text
        listed = await browser.get(ITEMS + query)
        assert listed.status_code == 200 and listed.json()["can_manage"] is True
        assert (await browser.get(f"{ITEMS}/{item}/file{query}")).status_code == 200
        assert (await remove(sessions, browser, item, query)).status_code == 204

    item = (await upload(sessions, admin, make_pdf())).json()["id"]
    # Plain staff are refused by the library; read_only by the console's write rule first.
    for browser, refusal in ((staff, "training_forbidden"), (reader, "write permission required")):
        for answer in (
            await upload(sessions, browser, make_pdf()),
            await patch(sessions, browser, item, {"title": "Taken over"}),
            await remove(sessions, browser, item),
        ):
            assert answer.status_code == 403, answer.text
            assert detail(answer) == refusal
        listed = await browser.get(ITEMS)
        assert listed.status_code == 200, listed.text
        assert listed.json()["can_manage"] is False
        assert [row["id"] for row in listed.json()["items"]] == [item]
        assert (await browser.get(f"{ITEMS}/{item}/file")).status_code == 200

    # An admin of another company sees their own library and cannot reach this one.
    own = await foreign_admin.get(ITEMS)
    assert own.status_code == 200 and own.json()["items"] == [] and own.json()["can_manage"]
    assert (await foreign_admin.get(ITEMS + q)).status_code == 403
    for answer in (
        await foreign_admin.get(f"{ITEMS}/{item}/file"),
        await patch(sessions, foreign_admin, item, {"title": "Taken over"}),
        await remove(sessions, foreign_admin, item),
    ):
        assert answer.status_code == 404 and detail(answer) == "training_not_found", answer.text

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sessions.app), base_url=ORIGIN
    ) as machine:
        headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": tenant}
        for answer in (
            await machine.get(ITEMS, headers=headers),
            await machine.get(f"{ITEMS}/{item}/file", headers=headers),
            await machine.post(ITEMS, headers=headers, files={"file": ("a.pdf", make_pdf())}),
            await machine.delete(f"{ITEMS}/{item}", headers=headers),
        ):
            assert answer.status_code == 403, answer.text
    assert [row["title"] for row in await rows(sessions, tenant)] == ["deck"]


async def test_hr_owner_cannot_be_claimed_from_request_input(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    owner, owner_me = await sessions.member()
    owner_id = owner_me["user"]["id"]
    await make_owner(sessions, tenant, owner_id)
    staff, _ = await sessions.member()
    item = (await upload(sessions, owner, make_pdf())).json()["id"]

    # The session lookup refuses identity headers outright.
    spoofed = await staff.patch(
        f"{ITEMS}/{item}",
        json={"title": "Claimed"},
        headers={**sessions.headers(staff), "X-Actor-Id": owner_id},
    )
    assert spoofed.status_code == 403, spoofed.text
    assert detail(spoofed) == "browser identity headers are forbidden"
    # Query parameters and other headers are ignored: the actor is the session's user.
    for answer in (
        await patch(sessions, staff, item, {"title": "Claimed"}, f"?hr_user_id={owner_id}"),
        await staff.patch(
            f"{ITEMS}/{item}",
            json={"title": "Claimed"},
            headers={**sessions.headers(staff), "X-Hr-User-Id": owner_id},
        ),
        await upload(sessions, staff, make_pdf(), query=f"?hr_user_id={owner_id}"),
        await remove(sessions, staff, item, f"?hr_user_id={owner_id}&user_id={owner_id}"),
    ):
        assert answer.status_code == 403 and detail(answer) == "training_forbidden", answer.text
    # A body field is not part of the patch at all.
    claimed = await patch(sessions, staff, item, {"title": "Claimed", "hr_user_id": owner_id})
    assert claimed.status_code == 422, claimed.text
    assert (await staff.get(ITEMS + f"?hr_user_id={owner_id}")).json()["can_manage"] is False
    assert [row["title"] for row in await rows(sessions, tenant)] == ["deck"]
    # The real owner can.
    assert (await patch(sessions, owner, item, {"title": "Owner's title"})).status_code == 200


async def test_list_shape_and_can_manage_flag(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    q = f"?client_id={tenant}"
    admin, _ = await sessions.member(role="tenant_admin")
    staff, _ = await sessions.member()
    first = (await upload(sessions, admin, make_pdf(2), "first.pdf")).json()
    assert (await patch(sessions, admin, first["id"], {"category": "Safety"})).status_code == 200
    second = (await upload(sessions, admin, make_pdf(1), "second.pdf")).json()
    assert (await patch(sessions, admin, second["id"], {"category": "safety"})).status_code == 200
    third = (await upload(sessions, sessions.admin, make_pdf(3), "third.pdf", query=q)).json()
    assert (
        await patch(sessions, sessions.admin, third["id"], {"category": "Onboarding"}, q)
    ).status_code == 200
    await sessions.q("UPDATE training_items SET summary='Synthetic' WHERE id=%s", first["id"])

    listed = await staff.get(ITEMS)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert set(body) == {"items", "categories", "can_manage"}
    assert body["can_manage"] is False
    # Matched case-insensitively: one entry per category.
    assert body["categories"] == ["Onboarding", "Safety"]
    items = body["items"]
    assert [row["id"] for row in items] == [third["id"], second["id"], first["id"]]
    assert all(set(row) == ITEM_FIELDS for row in items)
    by_id = {row["id"]: row for row in items}
    assert by_id[first["id"]]["page_count"] == 2
    assert by_id[first["id"]]["byte_size"] == len(make_pdf(2))
    assert by_id[first["id"]]["has_summary"] is True
    assert by_id[second["id"]]["has_summary"] is False
    assert by_id[first["id"]]["uploaded_by_name"] == "Synthetic Member"
    # A platform admin is not named to the company.
    assert by_id[third["id"]]["uploaded_by_name"] is None
    assert (await admin.get(ITEMS)).json()["can_manage"] is True
    assert len(await rows(sessions, tenant)) == 3


async def test_upload_stores_row_and_file_and_audits(
    sessions: SessionWorld, training_store: ObjectStore, tmp_path: Path
) -> None:
    tenant = await enable(sessions)
    admin, admin_me = await sessions.member(role="tenant_admin")
    data = make_pdf(3)
    created = await upload(sessions, admin, data, "Induction deck.pdf")
    assert created.status_code == 201, created.text
    item = created.json()
    assert set(item) == ITEM_FIELDS
    assert item["title"] == "Induction deck" and item["page_count"] == 3
    assert item["byte_size"] == len(data) and item["has_summary"] is False
    assert item["description"] == "" and item["category"] == ""
    assert item["uploaded_by_name"] == "Synthetic Member"
    [row] = await rows(sessions, tenant)
    assert str(row["id"]) == item["id"]
    assert row["kind"] == "pdf" and row["media_type"] == "application/pdf"
    assert row["original_name"] == "Induction deck.pdf"
    assert str(row["created_by"]) == admin_me["user"]["id"]
    assert len(row["sha256"]) == 64
    stored = row["storage_name"]
    assert stored == f"{UUID(stored.removesuffix('.pdf'))}.pdf"
    path = tmp_path / "training" / tenant / stored
    assert path.read_bytes() == data
    assert names(training_store, tenant) == [stored]
    [entry] = await audits(sessions, tenant, "training.item_added")
    assert entry["target_id"] == item["id"]
    assert str(entry["actor_id"]) == admin_me["user"]["id"]
    assert entry["details"] == {}


async def test_title_defaults_to_file_stem_and_landscape_picks_slides(
    sessions: SessionWorld,
) -> None:
    await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    wide = await upload(sessions, admin, make_pdf(2, size=WIDESCREEN), "Quarterly deck.pdf")
    assert wide.json()["title"] == "Quarterly deck" and wide.json()["view_mode"] == "slides"
    tall = await upload(sessions, admin, make_pdf(1, size=PORTRAIT), "handbook.PDF")
    assert tall.json()["title"] == "handbook" and tall.json()["view_mode"] == "document"
    long = await upload(sessions, admin, make_pdf(), "x" * 200 + ".pdf")
    assert long.status_code == 201, long.text
    assert long.json()["title"] == "x" * 120


async def test_patch_changes_fields_and_audits_item_edited(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = (await upload(sessions, admin, make_pdf(), "deck.pdf")).json()
    changed = await patch(
        sessions,
        admin,
        item["id"],
        {
            "title": "  Fire safety  ",
            "description": "For every new colleague.",
            "category": "  Safety ",
            "view_mode": "slides",
        },
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["title"] == "Fire safety" and body["category"] == "Safety"
    assert body["description"] == "For every new colleague." and body["view_mode"] == "slides"
    # Only the fields sent change.
    again = await patch(sessions, admin, item["id"], {"category": ""})
    assert again.status_code == 200 and again.json()["title"] == "Fire safety"
    assert again.json()["category"] == "" and again.json()["view_mode"] == "slides"
    [entry, _] = await audits(sessions, tenant, "training.item_edited")
    assert entry["target_id"] == item["id"] and entry["details"] == {}
    for bad in (
        {},
        {"title": "   "},
        {"title": "x" * 121},
        {"description": "x" * 1001},
        {"category": "x" * 61},
        {"view_mode": "video"},
        {"original_name": "evil.pdf"},
    ):
        refused = await patch(sessions, admin, item["id"], bad)
        assert refused.status_code == 422, (bad, refused.text)
    missing = await patch(sessions, admin, str(uuid4()), {"title": "Nobody"})
    assert missing.status_code == 404 and detail(missing) == "training_not_found"
    [row] = await rows(sessions, tenant)
    assert row["title"] == "Fire safety" and row["original_name"] == "deck.pdf"


def _one_per_code() -> list[tuple[str, str, bytes, str]]:
    """The Task 6 bad files, one per refusal code: each costs a child process."""
    seen: dict[str, tuple[str, str, bytes, str]] = {}
    for case in bad_files():
        seen.setdefault(case[3], case)
    return list(seen.values())


def _raw_upload(file_name: str, data: bytes) -> tuple[bytes, str]:
    """A hand-built multipart body: httpx percent-encodes control characters in names."""
    boundary = "synthetic-training-boundary"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    body = head + data + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


REFUSALS: list[Any] = [
    pytest.param("pdf", name, data, 422, code, id=label)
    for label, name, data, code in _one_per_code()
] + [
    pytest.param(
        "pdf",
        "big.pdf",
        b"%PDF-" + b"0" * (MAX_FILE_BYTES + 1 - 5),
        413,
        "training_file_too_large",
        id="25 MB + 1",
    ),
    pytest.param("pdf", "empty.pdf", b"", 422, "training_not_pdf", id="empty"),
    pytest.param("pdf", "../up.pdf", b"%PDF-", 422, "training_name_invalid", id="traversal"),
    pytest.param("raw", "deck\x07.pdf", b"", 422, "training_name_invalid", id="control character"),
    pytest.param("two", "a.pdf", b"", 422, "training_upload_invalid", id="two files"),
    pytest.param("field", "a.pdf", b"", 422, "training_upload_invalid", id="wrong field"),
    pytest.param("json", "", b"", 415, "training_upload_invalid", id="not multipart"),
]


@pytest.mark.parametrize(("shape", "name", "data", "status", "code"), REFUSALS)
async def test_upload_refusals(
    sessions: SessionWorld,
    training_store: ObjectStore,
    shape: str,
    name: str,
    data: bytes,
    status: int,
    code: str,
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    headers = {**sessions.headers(admin), "Idempotency-Key": str(uuid4())}
    valid = make_pdf()
    if shape == "pdf":
        answer = await upload(sessions, admin, data, name)
    elif shape == "raw":
        body, content_type = _raw_upload(name, valid)
        answer = await admin.post(
            ITEMS, content=body, headers={**headers, "Content-Type": content_type}
        )
    elif shape == "two":
        answer = await admin.post(
            ITEMS, files=[("file", ("a.pdf", valid)), ("file", ("b.pdf", valid))], headers=headers
        )
    elif shape == "field":
        answer = await admin.post(ITEMS, files={"files": ("a.pdf", valid)}, headers=headers)
    else:
        answer = await admin.post(ITEMS, json={"file": "a.pdf"}, headers=headers)
    assert answer.status_code == status, answer.text
    assert detail(answer) == code
    assert names(training_store, tenant) == []
    assert await rows(sessions, tenant) == []


async def test_retry_same_key_returns_first_item_one_file(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    key = str(uuid4())
    data = make_pdf(2)
    first = await upload(sessions, admin, data, "deck.pdf", key=key)
    again = await upload(sessions, admin, data, "deck.pdf", key=key)
    assert first.status_code == 201 and again.status_code == 201, again.text
    assert again.json() == first.json()
    assert len(await rows(sessions, tenant)) == 1
    assert len(names(training_store, tenant)) == 1
    assert len(await audits(sessions, tenant, "training.item_added")) == 1


async def test_same_key_other_content_conflicts(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    key = str(uuid4())
    assert (await upload(sessions, admin, make_pdf(1), key=key)).status_code == 201
    conflict = await upload(sessions, admin, make_pdf(2), key=key)
    assert conflict.status_code == 409 and detail(conflict) == "training_key_conflict"
    assert len(await rows(sessions, tenant)) == 1
    assert len(names(training_store, tenant)) == 1


@pytest.mark.parametrize("written", [False, True], ids=["before write", "after write"])
async def test_storage_failure_leaves_no_row_and_no_orphan(
    sessions: SessionWorld,
    training_store: ObjectStore,
    monkeypatch: pytest.MonkeyPatch,
    written: bool,
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    original = training_store.put

    def failing(tenant_id: UUID, name: str, data: bytes) -> None:
        if written:
            original(tenant_id, name, data)
        raise StorageError("synthetic storage outage")

    monkeypatch.setattr(training_store, "put", failing)
    answer = await upload(sessions, admin, make_pdf())
    assert answer.status_code == 503, answer.text
    assert detail(answer) == "training_storage_unavailable"
    assert names(training_store, tenant) == []
    assert await rows(sessions, tenant) == []
    monkeypatch.setattr(training_store, "put", original)
    assert (await upload(sessions, admin, make_pdf())).status_code == 201


async def test_quota_by_count_and_by_bytes(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    small = make_pdf(1)
    key = str(uuid4())
    assert (await upload(sessions, admin, small, key=key)).status_code == 201

    monkeypatch.setattr(limits, "MAX_ITEMS", 1)
    full = await upload(sessions, admin, make_pdf(1))
    assert full.status_code == 409 and detail(full) == "training_library_full", full.text
    # A retry of a stored upload is not a new item.
    assert (await upload(sessions, admin, small, key=key)).status_code == 201

    monkeypatch.setattr(limits, "MAX_ITEMS", 300)
    monkeypatch.setattr(limits, "MAX_TOTAL_BYTES", 2 * len(small) + 10)
    assert (await upload(sessions, admin, make_pdf(1), "fits.pdf")).status_code == 201
    over = await upload(sessions, admin, make_pdf(1), "over.pdf")
    assert over.status_code == 409 and detail(over) == "training_library_full", over.text
    assert len(await rows(sessions, tenant)) == 2
    assert len(names(training_store, tenant)) == 2


async def test_last_slot_race_one_wins(
    sessions: SessionWorld, training_store: ObjectStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    monkeypatch.setattr(limits, "MAX_ITEMS", 1)
    answers = await asyncio.gather(
        upload(sessions, admin, make_pdf(1), "one.pdf"),
        upload(sessions, admin, make_pdf(2), "two.pdf"),
    )
    assert sorted(answer.status_code for answer in answers) == [201, 409], [
        answer.text for answer in answers
    ]
    assert {detail(answer) for answer in answers if answer.status_code == 409} == {
        "training_library_full"
    }
    assert len(await rows(sessions, tenant)) == 1
    assert len(names(training_store, tenant)) == 1


async def test_remove_deletes_row_and_file_and_audits(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, admin_me = await sessions.member(role="tenant_admin")
    keep = (await upload(sessions, admin, make_pdf(1), "keep.pdf")).json()
    item = (await upload(sessions, admin, make_pdf(2), "gone.pdf")).json()
    assert len(names(training_store, tenant)) == 2
    removed = await remove(sessions, admin, item["id"])
    assert removed.status_code == 204, removed.text
    [kept] = await rows(sessions, tenant)
    assert str(kept["id"]) == keep["id"]
    assert names(training_store, tenant) == [kept["storage_name"]]
    [entry] = await audits(sessions, tenant, "training.item_removed")
    assert entry["target_id"] == item["id"] and entry["details"] == {}
    assert str(entry["actor_id"]) == admin_me["user"]["id"]
    again = await remove(sessions, admin, item["id"])
    assert again.status_code == 404 and detail(again) == "training_not_found"
    gone = await admin.get(f"{ITEMS}/{item['id']}/file")
    assert gone.status_code == 404 and detail(gone) == "training_not_found"


async def test_open_download_survives_removal(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    data = make_pdf(300)  # several 64 KB blocks
    item = (await upload(sessions, admin, data)).json()
    removed: list[int] = []

    async def remove_on_start(scope: Any, receive: Any, send: Any) -> None:
        async def hooked(message: Any) -> None:
            if message["type"] == "http.response.start" and not removed:
                # The response holds the open file; remove the item before any byte is sent.
                answer = await remove(sessions, admin, item["id"])
                removed.append(answer.status_code)
                assert names(training_store, tenant) == []
            await send(message)

        await sessions.app(scope, receive, hooked)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remove_on_start), base_url=ORIGIN, cookies=admin.cookies
    ) as reader:
        download = await reader.get(f"{ITEMS}/{item['id']}/file")
    assert removed == [204]
    assert download.status_code == 200, download.text
    assert download.content == data
    assert await rows(sessions, tenant) == []


async def test_file_route_headers(sessions: SessionWorld) -> None:
    await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    reader, _ = await sessions.member(role="read_only")
    data = make_pdf(2)
    item = (await upload(sessions, admin, data, "Induction deck.pdf")).json()
    response = await reader.get(f"{ITEMS}/{item['id']}/file")
    assert response.status_code == 200, response.text
    assert response.content == data
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["content-length"] == str(len(data))


async def test_missing_object_answers_503_and_logs_id_only(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    item = (await upload(sessions, admin, make_pdf(), "Secret plan.pdf")).json()
    assert (await patch(sessions, admin, item["id"], {"title": "Hidden title"})).status_code == 200
    [row] = await rows(sessions, tenant)
    training_store.delete(UUID(tenant), row["storage_name"])
    with capture_logs() as logs:
        answer = await admin.get(f"{ITEMS}/{item['id']}/file")
    assert answer.status_code == 503 and detail(answer) == "training_storage_unavailable"
    assert [entry for entry in logs if entry.get("item_id") == item["id"]], logs
    text = repr(logs)
    for secret in ("Secret plan", "Hidden title", row["storage_name"]):
        assert secret not in text


async def test_company_b_cannot_read_patch_delete_or_open_company_a_item(
    sessions: SessionWorld, training_store: ObjectStore, app_dsn: str
) -> None:
    tenant_a = await enable(sessions)
    tenant_b = await enable(sessions, sessions.tenants[1])
    admin_a, _ = await sessions.member(role="tenant_admin")
    admin_b, _ = await sessions.member(role="tenant_admin", tenant=tenant_b)
    staff_b, _ = await sessions.member(tenant=tenant_b)
    item = (await upload(sessions, admin_a, make_pdf(), "a.pdf")).json()
    [row] = await rows(sessions, tenant_a)

    for browser in (admin_b, staff_b):
        listed = await browser.get(ITEMS)
        assert listed.status_code == 200 and listed.json()["items"] == []
        assert (await browser.get(ITEMS + f"?client_id={tenant_a}")).status_code == 403
        opened = await browser.get(f"{ITEMS}/{item['id']}/file")
        assert opened.status_code == 404 and detail(opened) == "training_not_found"
    b = f"?client_id={tenant_b}"
    for answer in (
        await patch(sessions, admin_b, item["id"], {"title": "Taken"}),
        await remove(sessions, admin_b, item["id"]),
        # A platform admin acting on company B cannot reach A's item through B either.
        await sessions.admin.get(f"{ITEMS}/{item['id']}/file{b}"),
        await patch(sessions, sessions.admin, item["id"], {"title": "Taken"}, b),
        await remove(sessions, sessions.admin, item["id"], b),
    ):
        assert answer.status_code == 404 and detail(answer) == "training_not_found", answer.text
    [still] = await rows(sessions, tenant_a)
    assert str(still["id"]) == item["id"] and still["title"] == "a"
    assert names(training_store, tenant_a) == [row["storage_name"]]

    # Under company B's tenant setting a direct query returns none of A's rows.
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        await conn.execute("SELECT set_config('app.client_id', %s, true)", (tenant_b,))
        cursor = await conn.execute("SELECT id FROM training_items")
        assert await cursor.fetchall() == []
    # A's object name read against B's tenant path is not found.
    with pytest.raises(StorageNotFound):
        training_store.open(UUID(tenant_b), row["storage_name"])


async def test_removed_hr_owner_loses_manage_on_next_request(sessions: SessionWorld) -> None:
    tenant = await enable(sessions)
    owner, owner_me = await sessions.member()
    successor, successor_me = await sessions.member()
    await make_owner(sessions, tenant, owner_me["user"]["id"])
    item = (await upload(sessions, owner, make_pdf())).json()
    assert (await owner.get(ITEMS)).json()["can_manage"] is True
    assert (await patch(sessions, owner, item["id"], {"title": "First"})).status_code == 200

    await sessions.q("DELETE FROM hr_client_owners WHERE client_id=%s", tenant)
    refused = await patch(sessions, owner, item["id"], {"title": "Second"})
    assert refused.status_code == 403 and detail(refused) == "training_forbidden"
    assert (await owner.get(ITEMS)).json()["can_manage"] is False

    # Moved to someone else: the new owner manages, the old one still cannot.
    await make_owner(sessions, tenant, successor_me["user"]["id"])
    assert (await patch(sessions, successor, item["id"], {"title": "Third"})).status_code == 200
    assert (await remove(sessions, owner, item["id"])).status_code == 403
    # An owner turned read_only no longer manages either.
    await sessions.q(
        "UPDATE console_memberships SET role='read_only' WHERE user_id=%s AND client_id=%s",
        successor_me["user"]["id"],
        tenant,
    )
    assert (await successor.get(ITEMS)).json()["can_manage"] is False
    [row] = await rows(sessions, tenant)
    assert row["title"] == "Third"


async def test_cleanup_removes_only_unreferenced_matching_names(
    sessions: SessionWorld, training_store: ObjectStore
) -> None:
    tenant = await enable(sessions)
    admin, _ = await sessions.member(role="tenant_admin")
    assert (await upload(sessions, admin, make_pdf())).status_code == 201
    [row] = await rows(sessions, tenant)
    client = UUID(tenant)
    orphan, partial, other = f"{uuid4()}.pdf", f".{uuid4()}.pdf.part", f"{uuid4()}.docx"
    for name in (orphan, partial, other, "notes.txt"):
        training_store.put(client, name, b"synthetic")
    assert await cleanup(sessions.pool, training_store, client) == 2
    assert set(names(training_store, tenant)) == {row["storage_name"], other, "notes.txt"}
