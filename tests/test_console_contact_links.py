"""Staff routes to link and unlink two contacts and to settle link reviews (Task 5).

The routes sit under /console/v1/subscriptions and wrap csa.contacts.linking; the linking rules
themselves are tested in tests/test_contact_linking.py. Synthetic data only.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from csa.subscriptions.api import build_subscriptions_router

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

BASE = "/console/v1/subscriptions"
PRIVATE = ("moved", "email", "@private.test", "909090", "909091")


@pytest.fixture(autouse=True)
async def packages(sessions: SessionWorld) -> None:
    sessions.app.include_router(build_subscriptions_router())
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "subscriptions")


async def contact(
    w: SessionWorld,
    tenant: str,
    name: str,
    *,
    days_old: float = 1,
    phone: str | None = None,
    telegram_id: int | None = None,
) -> str:
    rows = await w.q(
        "INSERT INTO contacts (client_id, full_name, phone, email, telegram_id, created_at) "
        "VALUES (%s,%s,%s,%s,%s, now() - %s * interval '1 day') RETURNING id",
        tenant,
        name,
        phone,
        f"{uuid.uuid4()}@private.test",
        telegram_id,
        days_old,
    )
    return str(rows[0]["id"])


async def identity(w: SessionWorld, tenant: str, contact_id: str, channel: str) -> None:
    await w.q(
        "INSERT INTO contact_identities (client_id, channel, external_id, contact_id) "
        "VALUES (%s,%s,%s,%s)",
        tenant,
        channel,
        f"ext-{uuid.uuid4()}",
        contact_id,
    )


async def subscribe(w: SessionWorld, tenant: str, contact_id: str) -> str:
    rows = await w.q(
        "INSERT INTO subscriptions (client_id, contact_id, plan, price, started_at) "
        "VALUES (%s,%s,'kecil',15,now()) RETURNING id",
        tenant,
        contact_id,
    )
    return str(rows[0]["id"])


async def conversation(w: SessionWorld, tenant: str, contact_id: str) -> str:
    rows = await w.q(
        "INSERT INTO conversations (client_id, contact_id, channel, channel_thread) "
        "VALUES (%s,%s,'telegram',%s) RETURNING id",
        tenant,
        contact_id,
        f"thread-{uuid.uuid4()}",
    )
    return str(rows[0]["id"])


async def owner_of(w: SessionWorld, table: str, row_id: str) -> str:
    rows = await w.q(f"SELECT contact_id FROM {table} WHERE id=%s", row_id)  # type: ignore[arg-type]
    return str(rows[0]["contact_id"])


async def link(w: SessionWorld, client: httpx.AsyncClient, a: str, b: str) -> httpx.Response:
    return await w.mutate(client, f"{BASE}/links", {"contact_a": a, "contact_b": b})


async def open_review(
    w: SessionWorld, tenant: str, a: str, b: str, reason: str = "several_matches"
) -> str:
    rows = await w.q(
        "INSERT INTO contact_link_reviews (client_id, contact_a, contact_b, reason) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        tenant,
        a,
        b,
        reason,
    )
    return str(rows[0]["id"])


def assert_private(*responses: httpx.Response) -> None:
    for response in responses:
        for word in PRIVATE:
            assert word not in response.text, f"{word} leaked in {response.text}"


async def test_link_then_unlink_round_trip(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    tenant = w.tenants[0]
    holder = await contact(w, tenant, "Aisha", days_old=2, phone="+60123456789")
    shell = await contact(w, tenant, "Aisha W", days_old=1)
    await identity(w, tenant, holder, "telegram")
    await identity(w, tenant, shell, "whatsapp")
    sub = await subscribe(w, tenant, holder)
    thread = await conversation(w, tenant, shell)

    made = await link(w, staff, shell, holder)
    assert made.status_code == 201, made.text
    out = made.json()
    assert out["reason"] == "staff" and out["unlinked_at"] is None and out["can_unlink"] is True
    assert out["survivor"] == {
        "id": holder,
        "full_name": "Aisha",
        "phone": "+60123456789",
        "channels": ["telegram", "whatsapp"],
    }
    # The loser shell shows the channels it had before its identities moved.
    assert out["loser"] == {
        "id": shell,
        "full_name": "Aisha W",
        "phone": None,
        "channels": ["whatsapp"],
    }
    assert set(out) == {
        "id",
        "survivor",
        "loser",
        "reason",
        "created_at",
        "unlinked_at",
        "can_unlink",
    }
    assert await owner_of(w, "conversations", thread) == holder
    assert await owner_of(w, "subscriptions", sub) == holder
    [stored] = await w.q("SELECT reason, created_by FROM contact_links WHERE id=%s", out["id"])
    assert stored["reason"] == "staff" and str(stored["created_by"]) == me["user"]["id"]

    for who in (holder, shell):
        listed = await staff.get(f"{BASE}/links?contact_id={who}")
        assert listed.status_code == 200, listed.text
        assert [row["id"] for row in listed.json()] == [out["id"]]
        assert_private(listed)
    assert [r["id"] for r in (await staff.get(f"{BASE}/links")).json()] == [out["id"]]

    undone = await w.mutate(staff, f"{BASE}/links/{out['id']}/unlink")
    assert undone.status_code == 200, undone.text
    assert undone.json() == {"skipped": []}
    assert await owner_of(w, "conversations", thread) == shell
    assert await owner_of(w, "subscriptions", sub) == holder
    [row] = (await staff.get(f"{BASE}/links?contact_id={shell}")).json()
    assert row["unlinked_at"] is not None and row["can_unlink"] is False
    assert row["loser"]["channels"] == ["whatsapp"] and row["survivor"]["channels"] == ["telegram"]

    again = await w.mutate(staff, f"{BASE}/links/{out['id']}/unlink")
    assert again.status_code == 409 and again.json()["detail"] == "link_not_active"

    audited = await w.q(
        "SELECT action, actor_id, target_id FROM console_audit "
        "WHERE client_id=%s AND action LIKE 'contacts.link.%%' ORDER BY created_at",
        tenant,
    )
    assert [(r["action"], str(r["actor_id"]), str(r["target_id"])) for r in audited] == [
        ("contacts.link.created", me["user"]["id"], out["id"]),
        ("contacts.link.removed", me["user"]["id"], out["id"]),
    ]


async def test_unlink_reports_what_it_could_not_restore(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    tenant = w.tenants[0]
    holder = await contact(w, tenant, "Holder", days_old=3)
    shell = await contact(w, tenant, "Shell", days_old=1, telegram_id=909090)
    await subscribe(w, tenant, holder)
    made = await link(w, staff, holder, shell)
    assert made.status_code == 201, made.text
    # The survivor took the Telegram account; staff then changed it, so unlink leaves it.
    await w.q("UPDATE contacts SET telegram_id=909091 WHERE id=%s", holder)

    undone = await w.mutate(staff, f"{BASE}/links/{made.json()['id']}/unlink")
    assert undone.status_code == 200, undone.text
    assert undone.json() == {"skipped": ["survivor.telegram_id"]}
    [back] = await w.q("SELECT telegram_id FROM contacts WHERE id=%s", shell)
    assert back["telegram_id"] == 909090


async def test_link_validation_and_refusals(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    tenant, elsewhere = w.tenants
    one = await contact(w, tenant, "One", days_old=3, telegram_id=1001)
    two = await contact(w, tenant, "Two", days_old=2, telegram_id=1002)
    three = await contact(w, tenant, "Three", days_old=1)
    stranger = await contact(w, elsewhere, "Stranger")

    same = await link(w, staff, one, one)
    assert same.status_code == 422 and same.json()["detail"] == "same_contact"
    malformed = await w.mutate(staff, f"{BASE}/links", {"contact_a": "nope", "contact_b": one})
    assert malformed.status_code == 422
    for a, b in ((one, str(uuid.uuid4())), (one, stranger), (stranger, one)):
        missing = await link(w, staff, a, b)
        assert missing.status_code == 404 and missing.json()["detail"] == "contact_not_found"

    refused = await link(w, staff, one, two)
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "link_refused:telegram_conflict"
    await identity(w, tenant, one, "whatsapp")
    await identity(w, tenant, three, "whatsapp")
    refused = await link(w, staff, one, three)
    assert refused.json()["detail"] == "link_refused:same_channel_identity"

    merged = await link(w, staff, two, three)
    assert merged.status_code == 201, merged.text
    shell = merged.json()["loser"]["id"]
    for a, b in ((shell, one), (one, shell)):
        gone = await link(w, staff, a, b)
        assert gone.status_code == 409 and gone.json()["detail"] == "contact_merged"

    [count] = await w.q("SELECT count(*) AS n FROM contact_links WHERE client_id=%s", tenant)
    assert count["n"] == 1
    audited = await w.q(
        "SELECT count(*) AS n FROM console_audit WHERE client_id=%s AND action LIKE 'contacts.%%'",
        tenant,
    )
    assert audited[0]["n"] == 1


async def test_unlink_errors(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    other, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    tenant = w.tenants[0]
    a = await contact(w, tenant, "A", days_old=9)
    b = await contact(w, tenant, "B", days_old=8)
    c = await contact(w, tenant, "C", days_old=7)
    d = await contact(w, tenant, "D", days_old=6)
    await subscribe(w, tenant, d)

    first = (await link(w, staff, a, b)).json()  # A survives (older)
    second = (await link(w, staff, a, c)).json()  # A survives again, a later link on A
    assert first["can_unlink"] is True and second["can_unlink"] is True
    listed = {r["id"]: r for r in (await staff.get(f"{BASE}/links?contact_id={a}")).json()}
    assert listed[first["id"]]["can_unlink"] is False
    assert listed[second["id"]]["can_unlink"] is True
    later = await w.mutate(staff, f"{BASE}/links/{first['id']}/unlink")
    assert later.status_code == 409 and later.json()["detail"] == "later_link_active"

    # D holds a subscription, so linking it with A makes A a shell: A's first links are stuck.
    third = await link(w, staff, a, d)
    assert third.status_code == 201 and third.json()["survivor"]["id"] == d
    merged = await w.mutate(staff, f"{BASE}/links/{second['id']}/unlink")
    assert merged.status_code == 409 and merged.json()["detail"] == "survivor_merged"

    # Newest first undoes cleanly, and can_unlink follows what unlink would accept.
    assert (await w.mutate(staff, f"{BASE}/links/{third.json()['id']}/unlink")).status_code == 200
    now = {r["id"]: r["can_unlink"] for r in (await staff.get(f"{BASE}/links")).json()}
    assert now == {first["id"]: False, second["id"]: True, third.json()["id"]: False}
    assert (await w.mutate(staff, f"{BASE}/links/{second['id']}/unlink")).status_code == 200
    assert (await w.mutate(staff, f"{BASE}/links/{first['id']}/unlink")).status_code == 200

    assert (await w.mutate(staff, f"{BASE}/links/{uuid.uuid4()}/unlink")).status_code == 404
    unknown = await w.mutate(staff, f"{BASE}/links/{uuid.uuid4()}/unlink")
    assert unknown.json()["detail"] == "link_not_found"
    foreign = await w.mutate(other, f"{BASE}/links/{first['id']}/unlink")
    assert foreign.status_code == 404 and foreign.json()["detail"] == "link_not_found"
    assert (await w.mutate(staff, f"{BASE}/links/not-a-uuid/unlink")).status_code == 422


async def test_an_old_link_cannot_be_undone(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    tenant = w.tenants[0]
    a = await contact(w, tenant, "A", days_old=9)
    b = await contact(w, tenant, "B", days_old=8)
    made = (await link(w, staff, a, b)).json()
    recent = await w.q(
        "UPDATE contact_links SET created_at = now() - interval '29 days' WHERE id=%s RETURNING id",
        made["id"],
    )
    assert recent
    [row] = (await staff.get(f"{BASE}/links")).json()
    assert row["can_unlink"] is True

    await w.q(
        "UPDATE contact_links SET created_at = now() - interval '31 days' WHERE id=%s", made["id"]
    )
    [row] = (await staff.get(f"{BASE}/links")).json()
    assert row["can_unlink"] is False
    closed = await w.mutate(staff, f"{BASE}/links/{made['id']}/unlink")
    assert closed.status_code == 409 and closed.json()["detail"] == "unlink_window_closed"


async def test_contact_filter_covers_both_sides_and_is_scoped(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    other, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    tenant = w.tenants[0]
    a = await contact(w, tenant, "A", days_old=9)
    b = await contact(w, tenant, "B", days_old=8)
    c = await contact(w, tenant, "C", days_old=7)
    d = await contact(w, tenant, "D", days_old=6)
    ab = (await link(w, staff, a, b)).json()
    cd = (await link(w, staff, c, d)).json()

    assert [r["id"] for r in (await staff.get(f"{BASE}/links?contact_id={b}")).json()] == [ab["id"]]
    assert [r["id"] for r in (await staff.get(f"{BASE}/links?contact_id={a}")).json()] == [ab["id"]]
    both = await staff.get(f"{BASE}/links")
    assert [r["id"] for r in both.json()] == [cd["id"], ab["id"]]
    assert len((await staff.get(f"{BASE}/links?limit=1")).json()) == 1
    assert (await staff.get(f"{BASE}/links?limit=0")).status_code == 422

    assert (await other.get(f"{BASE}/links")).json() == []
    hidden = await other.get(f"{BASE}/links?contact_id={a}")
    assert hidden.status_code == 404 and hidden.json()["detail"] == "contact_not_found"
    assert (await staff.get(f"{BASE}/links?contact_id={uuid.uuid4()}")).status_code == 404


async def test_roles_package_and_tenant_selection(sessions: SessionWorld) -> None:
    w = sessions
    admin, _ = await w.member(role="tenant_admin")
    staff, _ = await w.member(role="tenant_staff")
    reader, _ = await w.member(role="read_only")
    tenant = w.tenants[0]
    a = await contact(w, tenant, "A", days_old=9)
    b = await contact(w, tenant, "B", days_old=8)
    c = await contact(w, tenant, "C", days_old=7)
    made = (await link(w, admin, a, b)).json()
    review = await open_review(w, tenant, a, c)

    for refused in (
        await link(w, reader, a, c),
        await w.mutate(reader, f"{BASE}/links/{made['id']}/unlink"),
        await w.mutate(
            reader, f"{BASE}/link-reviews/{review}/resolve", {"resolution": "dismissed"}
        ),
    ):
        assert refused.status_code == 403, refused.text
    assert (await reader.get(f"{BASE}/links")).status_code == 200
    assert (await reader.get(f"{BASE}/link-reviews")).status_code == 200
    [still] = await w.q("SELECT status FROM contact_link_reviews WHERE id=%s", review)
    assert still["status"] == "open"
    assert (await staff.get(f"{BASE}/links")).status_code == 200
    assert (await w.mutate(staff, f"{BASE}/links/{made['id']}/unlink")).status_code == 200

    crossing = f"client_id={w.tenants[1]}"
    for refused in (
        await admin.get(f"{BASE}/links?{crossing}"),
        await admin.get(f"{BASE}/link-reviews?{crossing}"),
        await w.mutate(admin, f"{BASE}/links?{crossing}", {"contact_a": a, "contact_b": c}),
        await w.mutate(admin, f"{BASE}/links/{made['id']}/unlink?{crossing}"),
        await w.mutate(
            admin, f"{BASE}/link-reviews/{review}/resolve?{crossing}", {"resolution": "dismissed"}
        ),
    ):
        assert refused.status_code == 403, refused.text
    named = await w.admin.get(f"{BASE}/links?{crossing}")
    assert named.status_code == 200 and named.json() == []

    created = await w.mutate(
        w.admin,
        "/console/v1/admin/clients",
        {"slug": f"synthetic-{uuid.uuid4()}", "name": "No Package Tenant"},
    )
    assert created.status_code == 201, created.text
    bare, _ = await w.member(role="tenant_admin", tenant=created.json()["id"])
    for refused in (
        await bare.get(f"{BASE}/links"),
        await bare.get(f"{BASE}/link-reviews"),
        await link(w, bare, a, b),
        await w.mutate(bare, f"{BASE}/links/{made['id']}/unlink"),
        await w.mutate(bare, f"{BASE}/link-reviews/{review}/resolve", {"resolution": "dismissed"}),
    ):
        assert refused.status_code == 403, refused.text


async def test_review_list_and_dismiss(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    reader, _ = await w.member(role="read_only")
    other, _ = await w.member(role="tenant_admin", tenant=w.tenants[1])
    tenant = w.tenants[0]
    a = await contact(w, tenant, "Ali", days_old=9, phone="+60111111111")
    b = await contact(w, tenant, "Ali B", days_old=8)
    c = await contact(w, tenant, "Chan", days_old=7)
    await identity(w, tenant, b, "whatsapp")
    first = await open_review(w, tenant, a, b, "several_matches")
    second = await open_review(w, tenant, b, c, "table_conflict")

    listed = await reader.get(f"{BASE}/link-reviews")
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [r["id"] for r in rows] == [second, first]
    shown = rows[1]
    assert set(shown) == {
        "id",
        "contact_a",
        "contact_b",
        "reason",
        "status",
        "resolution",
        "created_at",
    }
    assert shown["reason"] == "several_matches" and shown["status"] == "open"
    assert shown["resolution"] is None
    assert shown["contact_a"] == {
        "id": a,
        "full_name": "Ali",
        "phone": "+60111111111",
        "channels": [],
    }
    assert shown["contact_b"] == {
        "id": b,
        "full_name": "Ali B",
        "phone": None,
        "channels": ["whatsapp"],
    }
    assert_private(listed)
    assert (await reader.get(f"{BASE}/link-reviews?status=resolved")).json() == []
    assert (await reader.get(f"{BASE}/link-reviews?status=nope")).status_code == 422
    assert len((await reader.get(f"{BASE}/link-reviews?limit=1")).json()) == 1
    assert (await other.get(f"{BASE}/link-reviews")).json() == []

    foreign = await w.mutate(
        other, f"{BASE}/link-reviews/{first}/resolve", {"resolution": "dismissed"}
    )
    assert foreign.status_code == 404 and foreign.json()["detail"] == "review_not_found"

    bad = await w.mutate(staff, f"{BASE}/link-reviews/{first}/resolve", {"resolution": "merged"})
    assert bad.status_code == 422
    done = await w.mutate(
        staff, f"{BASE}/link-reviews/{first}/resolve", {"resolution": "dismissed"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "resolved" and done.json()["resolution"] == "dismissed"
    assert done.json()["id"] == first
    assert_private(done)
    [row] = await w.q(
        "SELECT status, resolution, resolved_by, resolved_at FROM contact_link_reviews WHERE id=%s",
        first,
    )
    assert row["status"] == "resolved" and row["resolution"] == "dismissed"
    assert str(row["resolved_by"]) == me["user"]["id"] and row["resolved_at"] is not None
    assert [r["id"] for r in (await reader.get(f"{BASE}/link-reviews")).json()] == [second]
    [gone] = (await reader.get(f"{BASE}/link-reviews?status=resolved")).json()
    assert gone["id"] == first and gone["resolution"] == "dismissed"
    assert await w.q("SELECT 1 FROM contact_links WHERE client_id=%s", tenant) == []

    again = await w.mutate(
        staff, f"{BASE}/link-reviews/{first}/resolve", {"resolution": "dismissed"}
    )
    assert again.status_code == 409 and again.json()["detail"] == "review_not_open"
    again = await w.mutate(staff, f"{BASE}/link-reviews/{first}/resolve", {"resolution": "linked"})
    assert again.status_code == 409 and again.json()["detail"] == "review_not_open"
    missing = await w.mutate(
        staff, f"{BASE}/link-reviews/{uuid.uuid4()}/resolve", {"resolution": "linked"}
    )
    assert missing.status_code == 404 and missing.json()["detail"] == "review_not_found"

    audited = await w.q(
        "SELECT action, actor_id, target_id FROM console_audit WHERE client_id=%s "
        "AND action LIKE 'contacts.review.%%'",
        tenant,
    )
    assert [(r["action"], str(r["actor_id"]), str(r["target_id"])) for r in audited] == [
        ("contacts.review.dismissed", me["user"]["id"], first)
    ]


async def test_review_resolved_as_linked_links_the_pair(sessions: SessionWorld) -> None:
    w = sessions
    staff, me = await w.member(role="tenant_staff")
    tenant = w.tenants[0]
    holder = await contact(w, tenant, "Holder", days_old=9)
    shell = await contact(w, tenant, "Shell", days_old=1)
    thread = await conversation(w, tenant, shell)
    await subscribe(w, tenant, holder)
    review = await open_review(w, tenant, shell, holder)

    done = await w.mutate(staff, f"{BASE}/link-reviews/{review}/resolve", {"resolution": "linked"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "resolved" and done.json()["resolution"] == "linked"
    assert await owner_of(w, "conversations", thread) == holder
    [link_row] = await w.q(
        "SELECT survivor_id, loser_id, reason, created_by FROM contact_links WHERE client_id=%s",
        tenant,
    )
    assert (str(link_row["survivor_id"]), str(link_row["loser_id"])) == (holder, shell)
    assert link_row["reason"] == "staff" and str(link_row["created_by"]) == me["user"]["id"]
    [row] = await w.q("SELECT resolved_by FROM contact_link_reviews WHERE id=%s", review)
    assert str(row["resolved_by"]) == me["user"]["id"]
    audited = await w.q(
        "SELECT action, target_id FROM console_audit WHERE client_id=%s "
        "AND action LIKE 'contacts.%%'",
        tenant,
    )
    assert sorted((r["action"], str(r["target_id"])) for r in audited) == [
        ("contacts.review.linked", review)
    ]


async def test_review_linked_refusal_leaves_it_open(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    tenant = w.tenants[0]
    one = await contact(w, tenant, "One", days_old=3, telegram_id=1001)
    two = await contact(w, tenant, "Two", days_old=2, telegram_id=1002)
    review = await open_review(w, tenant, one, two, "telegram_conflict")

    refused = await w.mutate(
        staff, f"{BASE}/link-reviews/{review}/resolve", {"resolution": "linked"}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "link_refused:telegram_conflict"
    [row] = await w.q("SELECT status, resolution FROM contact_link_reviews WHERE id=%s", review)
    assert row["status"] == "open" and row["resolution"] is None
    assert await w.q("SELECT 1 FROM contact_links WHERE client_id=%s", tenant) == []
    assert await w.q("SELECT 1 FROM console_audit WHERE action LIKE 'contacts.%%'") == []

    # Staff can still dismiss it.
    dismissed = await w.mutate(
        staff, f"{BASE}/link-reviews/{review}/resolve", {"resolution": "dismissed"}
    )
    assert dismissed.status_code == 200, dismissed.text


async def test_a_review_of_a_merged_contact_cannot_be_linked(sessions: SessionWorld) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    tenant = w.tenants[0]
    a = await contact(w, tenant, "A", days_old=9)
    b = await contact(w, tenant, "B", days_old=8)
    c = await contact(w, tenant, "C", days_old=7)
    shell = (await link(w, staff, a, b)).json()["loser"]["id"]
    review = await open_review(w, tenant, shell, c)

    stale = await w.mutate(staff, f"{BASE}/link-reviews/{review}/resolve", {"resolution": "linked"})
    assert stale.status_code == 409 and stale.json()["detail"] == "contact_merged"
    [row] = await w.q("SELECT status FROM contact_link_reviews WHERE id=%s", review)
    assert row["status"] == "open"


async def test_static_link_paths_are_not_mistaken_for_a_subscription_id(
    sessions: SessionWorld,
) -> None:
    w = sessions
    staff, _ = await w.member(role="tenant_staff")
    for path in ("links", "link-reviews"):
        shown: Any = await staff.get(f"{BASE}/{path}")
        assert shown.status_code == 200 and shown.json() == []
