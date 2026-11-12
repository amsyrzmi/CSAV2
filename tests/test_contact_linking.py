"""Linking two contacts into one (csa.contacts.linking): verified phones, re-pointing every
contact-owned row to the survivor, refusals to review, unlink, tenancy and a race with ingress.

Runs as csa_app inside tx() against a disposable database; seeds are written as the owner.
Synthetic data only.
"""

from __future__ import annotations

import asyncio
import typing
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import pytest
from csa.contacts.linking import (
    EXCLUDED_TABLES,
    LINKED_TABLES,
    link_contacts,
    live_contact,
    unlink_contacts,
    verify_phone,
)
from csa.core import db
from csa.core.tenancy import tx
from fastapi import HTTPException
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner

pytestmark = requires_docker

# Primary key of each linked table as the link records it, and every unique index that includes
# contact_id (other columns, partial predicate). A migration that changes either must change the
# linking service (its conflict check before moving) and this table.
EXPECTED_KEYS: dict[str, tuple[tuple[str, ...], set[str]]] = {
    "conversations": (("id",), set()),
    "orders": (("id",), set()),
    "order_confirmations": (("id",), set()),
    "tickets": (("id",), set()),
    "warranty_claims": (("id",), set()),
    "pending_actions": (("id",), set()),
    "subscriptions": (("id",), set()),
    "bookings": (("id",), set()),
    "invoices": (("id",), set()),
    "agent_preparation_requests": (
        ("id",),
        {"(client_id, contact_id, agent, idempotency_key)"},
    ),
    "signup_requests": (
        ("id",),
        {"(client_id, contact_id, plan_code) WHERE (status = 'pending'::text)"},
    ),
    "contact_identities": (("client_id", "channel", "external_id"), set()),
}


@dataclass
class Shop:
    owner_dsn: str
    pool: AsyncConnectionPool
    client: UUID
    other: UUID
    user: UUID


@pytest.fixture
async def shop(migrated_dsn: str, app_dsn: str) -> AsyncIterator[Shop]:
    client, other, user = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with _owner(migrated_dsn) as conn:
        for cid in (client, other):
            conn.execute(
                "INSERT INTO clients (id, slug, name, platform_version)"
                " VALUES (%s, %s, 'Synthetic Co', 'test')",
                (cid, f"link-{cid}"),
            )
        conn.execute(
            "INSERT INTO console_users (id, email, display_name) VALUES (%s, %s, 'Synthetic')",
            (user, f"{user}@example.test"),
        )
    pool = db.build_pool(app_dsn, max_size=4)
    await pool.open(wait=True)
    try:
        yield Shop(migrated_dsn, pool, client, other, user)
    finally:
        await pool.close()


def contact(shop: Shop, *, days_old: float, client: UUID | None = None, **cols: Any) -> UUID:
    row: dict[str, Any] = {"id": uuid.uuid4(), "client_id": client or shop.client, **cols}
    names, slots = ", ".join(row), ", ".join(["%s"] * len(row))
    with _owner(shop.owner_dsn) as conn:
        conn.execute(
            f"INSERT INTO contacts ({names}, created_at)"  # type: ignore[arg-type]
            f" VALUES ({slots}, now() - %s * interval '1 day')",
            (*row.values(), days_old),
        )
    return row["id"]


def identity(shop: Shop, contact_id: UUID, channel: str, external_id: str) -> None:
    with _owner(shop.owner_dsn) as conn:
        conn.execute(
            "INSERT INTO contact_identities (client_id, channel, external_id, contact_id)"
            " VALUES (%s, %s, %s, %s)",
            (shop.client, channel, external_id, contact_id),
        )


def subscribe(shop: Shop, contact_id: UUID) -> str:
    with _owner(shop.owner_dsn) as conn:
        [row] = conn.execute(
            "INSERT INTO subscriptions (client_id, contact_id, plan, price, started_at)"
            " VALUES (%s, %s, 'kecil', 15, now()) RETURNING id::text",
            (shop.client, contact_id),
        )
    return row["id"]


def conversation(shop: Shop, contact_id: UUID) -> str:
    with _owner(shop.owner_dsn) as conn:
        [row] = conn.execute(
            "INSERT INTO conversations (client_id, contact_id, channel, channel_thread)"
            " VALUES (%s, %s, 'telegram', %s) RETURNING id::text",
            (shop.client, contact_id, f"thread-{uuid.uuid4()}"),
        )
    return row["id"]


def prepared_request(shop: Shop, contact_id: UUID, key: str) -> None:
    with _owner(shop.owner_dsn) as conn:
        conn.execute(
            "INSERT INTO agent_preparation_requests (client_id, contact_id, agent, action,"
            " idempotency_key, payload, trace_id, requested_by)"
            " VALUES (%s, %s, 'subscription_agent', 'request_pause', %s, '{}', %s, 'test')",
            (shop.client, contact_id, key, uuid.uuid4()),
        )


def seed_everything(shop: Shop, contact_id: UUID) -> dict[str, list[Any]]:
    """One row in every linked table for the contact; returns the primary keys per table."""
    tag = uuid.uuid4().hex[:10]
    c, k = shop.client, contact_id
    ids: dict[str, list[Any]] = {}
    with _owner(shop.owner_dsn) as conn:

        def one(table: str, sql: str, params: tuple[Any, ...]) -> Any:
            [row] = conn.execute(sql, params)  # type: ignore[arg-type]
            ids[table] = [row["pk"]]
            return row["pk"]

        convo = one(
            "conversations",
            "INSERT INTO conversations (client_id, contact_id, channel, channel_thread)"
            " VALUES (%s, %s, 'telegram', %s) RETURNING id::text AS pk",
            (c, k, f"t-{tag}"),
        )
        order = one(
            "orders",
            "INSERT INTO orders (id, client_id, contact_id, status, total)"
            " VALUES (%s, %s, %s, 'confirmed', 10) RETURNING id AS pk",
            (f"ORD-{tag}", c, k),
        )
        one(
            "order_confirmations",
            "INSERT INTO order_confirmations (client_id, order_id, contact_id, token, expires_at)"
            " VALUES (%s, %s, %s, %s, now() + interval '1 day') RETURNING id::text AS pk",
            (c, order, k, f"tok-{tag}"),
        )
        one(
            "tickets",
            "INSERT INTO tickets (client_id, conversation_id, contact_id, subject, category)"
            " VALUES (%s, %s, %s, 'Late parcel', 'delivery') RETURNING id AS pk",
            (c, convo, k),
        )
        one(
            "warranty_claims",
            "INSERT INTO warranty_claims (client_id, contact_id, order_id, sku, issue)"
            " VALUES (%s, %s, %s, 'SKU-1', 'Cracked') RETURNING id AS pk",
            (c, k, order),
        )
        one(
            "pending_actions",
            "INSERT INTO pending_actions (client_id, conversation_id, contact_id, agent, action,"
            " expires_at) VALUES (%s, %s, %s, 'order_agent', 'confirm', now() + interval '1 hour')"
            " RETURNING id::text AS pk",
            (c, convo, k),
        )
        one(
            "subscriptions",
            "INSERT INTO subscriptions (client_id, contact_id, plan, price, started_at)"
            " VALUES (%s, %s, 'kecil', 15, now()) RETURNING id::text AS pk",
            (c, k),
        )
        one(
            "bookings",
            "INSERT INTO bookings (client_id, contact_id, service, starts_at, ends_at)"
            " VALUES (%s, %s, 'Haircut', now() + interval '1 day', now() + interval '25 hours')"
            " RETURNING id::text AS pk",
            (c, k),
        )
        one(
            "invoices",
            "INSERT INTO invoices (id, client_id, contact_id, order_id, status, amount, issued_at)"
            " VALUES (%s, %s, %s, %s, 'open', 10, now()) RETURNING id AS pk",
            (f"INV-{tag}", c, k, order),
        )
        one(
            "agent_preparation_requests",
            "INSERT INTO agent_preparation_requests (client_id, contact_id, agent, action,"
            " idempotency_key, payload, trace_id, requested_by)"
            " VALUES (%s, %s, 'subscription_agent', 'request_pause', %s, '{}', %s, 'test')"
            " RETURNING id::text AS pk",
            (c, k, f"idem-{tag}", uuid.uuid4()),
        )
        one(
            "signup_requests",
            "INSERT INTO signup_requests (client_id, contact_id, plan_code, full_name, phone)"
            " VALUES (%s, %s, 'kecil', 'Siti Contoh', '+60123456789') RETURNING id::text AS pk",
            (c, k),
        )
        one(
            "contact_identities",
            "INSERT INTO contact_identities (client_id, channel, external_id, contact_id)"
            " VALUES (%s, 'telegram', %s, %s)"
            " RETURNING jsonb_build_array(client_id, channel, external_id) AS pk",
            (c, f"tg-{tag}", k),
        )
    return ids


def owned(shop: Shop, contact_id: UUID) -> dict[str, list[Any]]:
    """Primary keys of every linked-table row the contact owns, in the link's recorded shape."""
    out: dict[str, list[Any]] = {}
    with _owner(shop.owner_dsn) as conn:
        for table in LINKED_TABLES:
            pk = (
                "jsonb_build_array(client_id, channel, external_id)"
                if table == "contact_identities"
                else "to_jsonb(id)"
            )
            query = f"SELECT {pk} AS pk FROM {table} WHERE contact_id = %s ORDER BY 1"
            rows = conn.execute(query, (contact_id,)).fetchall()  # type: ignore[arg-type]
            out[table] = [r["pk"] for r in rows]
    return out


def contact_row(shop: Shop, contact_id: UUID) -> dict[str, Any]:
    with _owner(shop.owner_dsn) as conn:
        [row] = conn.execute(
            "SELECT phone, phone_digits, phone_verified_at, telegram_id, merged_into, email"
            " FROM contacts WHERE id = %s",
            (contact_id,),
        )
    return row


def rows(shop: Shop, sql: str, *params: Any) -> list[dict[str, Any]]:
    with _owner(shop.owner_dsn) as conn:
        return conn.execute(sql, params).fetchall()  # type: ignore[arg-type]


async def link(shop: Shop, a: UUID, b: UUID, *, client: UUID | None = None) -> Any:
    cid = client or shop.client
    async with tx(shop.pool, str(cid)) as conn:
        return await link_contacts(conn, cid, a, b, reason="staff", actor_user_id=shop.user)


async def unlink(shop: Shop, link_id: UUID) -> Any:
    async with tx(shop.pool, str(shop.client)) as conn:
        return await unlink_contacts(conn, shop.client, link_id, shop.user)


async def verify(shop: Shop, contact_id: UUID, phone: str, *, client: UUID | None = None) -> Any:
    cid = client or shop.client
    async with tx(shop.pool, str(cid)) as conn:
        return await verify_phone(conn, cid, contact_id, phone, source="whatsapp")


LOCK = "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))"


async def until_waiting(shop: Shop) -> bool:
    """Whether some transaction comes to wait for an advisory lock within about three seconds."""
    for _ in range(30):
        [row] = rows(
            shop, "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
        )
        if row["n"]:
            return True
        await asyncio.sleep(0.1)
    return False


def set_columns(shop: Shop, contact_id: UUID, **cols: Any) -> None:
    """A later change to a contact (staff or ingress), written as the owner."""
    names = ", ".join(f"{k} = %s" for k in cols)
    with _owner(shop.owner_dsn) as conn:
        conn.execute(
            f"UPDATE contacts SET {names} WHERE id = %s",  # type: ignore[arg-type]
            (*cols.values(), contact_id),
        )


def open_reviews(shop: Shop) -> list[dict[str, Any]]:
    return rows(
        shop,
        "SELECT contact_a, contact_b, reason FROM contact_link_reviews"
        " WHERE client_id = %s AND status = 'open'",
        shop.client,
    )


# ------------------------------------------------------------------------------------------------
# The schema the re-pointing relies on


def test_linked_tables_keys_and_grants_are_the_ones_the_service_knows(migrated_dsn: str) -> None:
    assert set(LINKED_TABLES) == set(EXPECTED_KEYS)
    with _owner(migrated_dsn) as conn:
        for table, (pk, uniques) in EXPECTED_KEYS.items():
            [key] = conn.execute(
                "SELECT array_agg(a.attname ORDER BY k.n) AS cols"
                " FROM pg_constraint c, unnest(c.conkey) WITH ORDINALITY AS k(attnum, n)"
                " JOIN pg_attribute a ON a.attnum = k.attnum"
                " WHERE c.conrelid = ('public.' || %s)::regclass AND c.contype = 'p'"
                " AND a.attrelid = c.conrelid",
                (table,),
            )
            assert tuple(key["cols"]) == pk, table
            found = conn.execute(
                "SELECT regexp_replace(pg_get_indexdef(i.indexrelid), '^.* USING btree ', '') AS d"
                " FROM pg_index i JOIN pg_attribute a"
                " ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)"
                " WHERE i.indrelid = ('public.' || %s)::regclass AND i.indisunique"
                " AND a.attname = 'contact_id'",
                (table,),
            ).fetchall()
            assert {r["d"] for r in found} == uniques, table
            [grant] = conn.execute(
                "SELECT has_column_privilege('csa_app', ('public.' || %s)::regclass,"
                " 'contact_id', 'UPDATE') AS ok",
                (table,),
            )
            assert grant["ok"], table


def test_every_table_that_points_at_a_contact_is_linked_or_excluded(migrated_dsn: str) -> None:
    """A new table with a contact_id column, or a foreign key to contacts, must be decided on."""
    with _owner(migrated_dsn) as conn:
        found = conn.execute(
            "SELECT c.table_name AS t FROM information_schema.columns c"
            " JOIN information_schema.tables t"
            " ON t.table_schema = c.table_schema AND t.table_name = c.table_name"
            " WHERE c.table_schema = 'public' AND c.column_name = 'contact_id'"
            " AND t.table_type = 'BASE TABLE'"
            " UNION SELECT conrelid::regclass::text FROM pg_constraint"
            " WHERE contype = 'f' AND confrelid = 'public.contacts'::regclass"
        ).fetchall()
    # contacts itself (merged_into) is what a link changes, not a row that moves.
    tables = {r["t"].removeprefix("public.") for r in found} - {"contacts"}
    assert not set(LINKED_TABLES) & set(EXCLUDED_TABLES)
    assert tables == set(LINKED_TABLES) | set(EXCLUDED_TABLES)


def test_prepared_requests_stay_immutable_except_their_contact(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT has_table_privilege('csa_app', 'public.agent_preparation_requests', 'UPDATE')"
            " AS whole, has_table_privilege('csa_app', 'public.agent_preparation_requests',"
            " 'DELETE') AS del, has_column_privilege('csa_app',"
            " 'public.agent_preparation_requests', 'payload', 'UPDATE') AS payload"
        )
        assert row == {"whole": False, "del": False, "payload": False}
        [trigger] = conn.execute(
            "SELECT pg_get_triggerdef(oid) AS d FROM pg_trigger"
            " WHERE tgname = 'trg_preparation_contact_tenant'"
        )
        assert "BEFORE INSERT OR UPDATE OF client_id, contact_id" in trigger["d"]


# ------------------------------------------------------------------------------------------------
# link_contacts


async def test_link_repoints_every_linked_table_and_records_the_ids(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    subscribe(shop, survivor)
    kept = owned(shop, survivor)
    loser = contact(shop, days_old=1, telegram_id=777001)
    moved = seed_everything(shop, loser)
    assert set(moved) == set(LINKED_TABLES)

    out = await link(shop, survivor, loser)

    assert out.linked and out.refused is None and out.survivor_id == survivor
    assert owned(shop, loser) == {t: [] for t in LINKED_TABLES}
    after = owned(shop, survivor)
    for table in LINKED_TABLES:
        assert sorted(map(str, after[table])) == sorted(map(str, kept[table] + moved[table]))
    [audit] = rows(
        shop,
        "SELECT * FROM contact_links WHERE id = %s AND client_id = %s",
        out.link_id,
        shop.client,
    )
    assert audit["survivor_id"] == survivor and audit["loser_id"] == loser
    assert audit["reason"] == "staff" and audit["created_by"] == shop.user
    for table in LINKED_TABLES:
        assert audit["moved"][table] == moved[table], table
    assert set(audit["moved"]["contacts"]) == {"survivor", "loser", "linked"}
    assert set(audit["moved"]["contacts"]["linked"]) == {"survivor", "loser"}
    assert contact_row(shop, loser)["merged_into"] == survivor


async def test_survivor_is_the_subscription_holder_else_the_older(shop: Shop) -> None:
    older, newer = contact(shop, days_old=20), contact(shop, days_old=2)
    subscribe(shop, newer)
    assert (await link(shop, older, newer)).survivor_id == newer
    assert contact_row(shop, older)["merged_into"] == newer

    older, newer = contact(shop, days_old=20), contact(shop, days_old=2)
    out = await link(shop, newer, older)
    assert out.survivor_id == older
    assert contact_row(shop, newer)["merged_into"] == older


async def test_telegram_id_moves_to_a_survivor_without_one(shop: Shop) -> None:
    survivor = contact(shop, days_old=9, phone="+60 11 1111 1111", email="siti@example.test")
    loser = contact(
        shop, days_old=1, telegram_id=777002, phone="+60 12 2222 2222", email="s2@example.test"
    )
    identity(shop, loser, "telegram", "777002")
    identity(shop, survivor, "whatsapp", "601111111111")

    out = await link(shop, survivor, loser)

    assert out.linked
    s, lo = contact_row(shop, survivor), contact_row(shop, loser)
    assert s["telegram_id"] == 777002 and lo["telegram_id"] is None
    # The survivor keeps its own phone (its digits do not change) and its own email.
    assert s["phone"] == "+60 11 1111 1111" and s["phone_digits"] == "601111111111"
    assert s["email"] == "siti@example.test" and lo["email"] == "s2@example.test"
    channels = rows(
        shop,
        "SELECT channel FROM contact_identities WHERE contact_id = %s ORDER BY channel",
        survivor,
    )
    assert [r["channel"] for r in channels] == ["telegram", "whatsapp"]


async def test_survivor_without_a_phone_takes_the_losers(shop: Shop) -> None:
    survivor = contact(shop, days_old=9)
    loser = contact(
        shop, days_old=1, phone="+60123456789", phone_verified_at="2026-11-01T00:00:00Z"
    )
    await link(shop, survivor, loser)
    s = contact_row(shop, survivor)
    assert s["phone"] == "+60123456789" and s["phone_verified_at"] is not None


async def test_two_telegram_accounts_refuse_and_move_nothing(shop: Shop) -> None:
    survivor = contact(shop, days_old=9, telegram_id=777003)
    loser = contact(shop, days_old=1, telegram_id=777004)
    seeded = seed_everything(shop, loser)

    out = await link(shop, survivor, loser)

    assert not out.linked and out.refused == "telegram_conflict" and out.link_id is None
    assert owned(shop, loser) == seeded
    assert contact_row(shop, loser)["merged_into"] is None
    assert contact_row(shop, survivor)["telegram_id"] == 777003
    assert rows(shop, "SELECT id FROM contact_links WHERE client_id = %s", shop.client) == []


async def test_an_identity_on_a_channel_the_survivor_holds_refuses(shop: Shop) -> None:
    survivor, loser = contact(shop, days_old=9), contact(shop, days_old=1)
    identity(shop, survivor, "whatsapp", "60111111111")
    identity(shop, loser, "whatsapp", "60122222222")

    out = await link(shop, survivor, loser)

    assert not out.linked and out.refused == "same_channel_identity"
    assert contact_row(shop, loser)["merged_into"] is None
    assert len(owned(shop, loser)["contact_identities"]) == 1


async def test_a_colliding_unique_key_refuses_before_moving_anything(shop: Shop) -> None:
    survivor, loser = contact(shop, days_old=9), contact(shop, days_old=1)
    prepared_request(shop, survivor, "same-key")
    seeded = seed_everything(shop, loser)
    prepared_request(shop, loser, "same-key")
    before = owned(shop, loser)

    out = await link(shop, survivor, loser)

    assert not out.linked and out.refused == "table_conflict"
    assert owned(shop, loser) == before
    assert len(before["agent_preparation_requests"]) == 2 and seeded
    assert contact_row(shop, loser)["merged_into"] is None


async def test_company_b_cannot_link_company_a_contacts(shop: Shop) -> None:
    a1, a2 = contact(shop, days_old=9), contact(shop, days_old=1)
    with pytest.raises(HTTPException) as err:
        await link(shop, a1, a2, client=shop.other)
    assert err.value.status_code == 404 and err.value.detail == "contact_not_found"
    b1 = contact(shop, days_old=1, client=shop.other)
    with pytest.raises(HTTPException) as err:
        await link(shop, b1, a2, client=shop.other)
    assert err.value.status_code == 404
    with pytest.raises(HTTPException) as err:
        await verify(shop, a1, "0123456789", client=shop.other)
    assert err.value.status_code == 404
    assert contact_row(shop, a2)["merged_into"] is None
    assert contact_row(shop, a1)["phone"] is None


async def test_a_contact_cannot_be_linked_to_itself_or_twice(shop: Shop) -> None:
    survivor, loser = contact(shop, days_old=9), contact(shop, days_old=1)
    with pytest.raises(HTTPException) as err:
        await link(shop, survivor, survivor)
    assert err.value.status_code == 422
    await link(shop, survivor, loser)
    with pytest.raises(HTTPException) as err:
        await link(shop, survivor, loser)
    assert err.value.status_code == 409 and err.value.detail == "contact_merged"


# ------------------------------------------------------------------------------------------------
# unlink_contacts


async def test_unlink_restores_every_moved_row_and_keeps_later_rows(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    subscribe(shop, survivor)
    identity(shop, survivor, "whatsapp", "60111111111")
    kept = owned(shop, survivor)
    loser = contact(
        shop,
        days_old=1,
        telegram_id=777005,
        phone="+60123456789",
        phone_verified_at="2026-11-01T00:00:00Z",
    )
    moved = seed_everything(shop, loser)
    before = {"survivor": contact_row(shop, survivor), "loser": contact_row(shop, loser)}

    out = await link(shop, survivor, loser)
    assert contact_row(shop, survivor)["telegram_id"] == 777005
    later = conversation(shop, survivor)

    await unlink(shop, out.link_id)

    assert owned(shop, loser) == moved
    after = owned(shop, survivor)
    assert after["conversations"] == sorted(kept["conversations"] + [later])
    for table in LINKED_TABLES:
        if table != "conversations":
            assert after[table] == kept[table], table
    assert contact_row(shop, survivor) == before["survivor"]
    assert contact_row(shop, loser) == before["loser"]
    [audit] = rows(
        shop, "SELECT unlinked_at, unlinked_by FROM contact_links WHERE id = %s", out.link_id
    )
    assert audit["unlinked_at"] is not None and audit["unlinked_by"] == shop.user

    with pytest.raises(HTTPException) as err:
        await unlink(shop, out.link_id)
    assert err.value.status_code == 409 and err.value.detail == "link_not_active"


async def test_unlink_after_31_days_is_refused(shop: Shop) -> None:
    survivor, loser = contact(shop, days_old=60), contact(shop, days_old=40)
    out = await link(shop, survivor, loser)
    with _owner(shop.owner_dsn) as conn:
        conn.execute(
            "UPDATE contact_links SET created_at = now() - interval '31 days' WHERE id = %s",
            (out.link_id,),
        )
    with pytest.raises(HTTPException) as err:
        await unlink(shop, out.link_id)
    assert err.value.status_code == 409 and err.value.detail == "unlink_window_closed"
    assert contact_row(shop, loser)["merged_into"] == survivor


async def test_unlink_of_another_companys_link_is_not_found(shop: Shop) -> None:
    survivor, loser = contact(shop, days_old=9), contact(shop, days_old=1)
    out = await link(shop, survivor, loser)
    with pytest.raises(HTTPException) as err:
        async with tx(shop.pool, str(shop.other)) as conn:
            await unlink_contacts(conn, shop.other, out.link_id, shop.user)
    assert err.value.status_code == 404 and err.value.detail == "link_not_found"


async def test_stacked_links_unlink_newest_first_only(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    first_loser = contact(
        shop,
        days_old=10,
        phone="+60123456789",
        phone_verified_at="2026-11-01T00:00:00Z",
        telegram_id=777010,
    )
    second_loser = contact(shop, days_old=1)
    first = await link(shop, survivor, first_loser)
    second = await link(shop, survivor, second_loser)
    assert first.survivor_id == second.survivor_id == survivor
    assert contact_row(shop, survivor)["phone"] == "+60123456789"

    # The older link cannot be undone while a later one on the same survivor is active.
    with pytest.raises(HTTPException) as err:
        await unlink(shop, first.link_id)
    assert err.value.status_code == 409 and err.value.detail == "later_link_active"
    assert contact_row(shop, first_loser)["merged_into"] == survivor

    await unlink(shop, second.link_id)
    await unlink(shop, first.link_id)

    assert contact_row(shop, second_loser)["merged_into"] is None
    back = contact_row(shop, first_loser)
    assert back["merged_into"] is None and back["telegram_id"] == 777010
    assert back["phone"] == "+60123456789" and back["phone_verified_at"] is not None
    s = contact_row(shop, survivor)
    assert s["phone"] is None and s["phone_verified_at"] is None and s["telegram_id"] is None


async def test_unlink_keeps_a_phone_set_on_the_survivor_after_the_link(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    loser = contact(
        shop, days_old=1, phone="+60123456789", phone_verified_at="2026-11-01T00:00:00Z"
    )
    out = await link(shop, survivor, loser)
    set_columns(shop, survivor, phone="+60 19 888 7777", phone_verified_at="2026-11-05T00:00:00Z")
    newer = contact_row(shop, survivor)

    await unlink(shop, out.link_id)

    s = contact_row(shop, survivor)
    assert (s["phone"], s["phone_verified_at"]) == (newer["phone"], newer["phone_verified_at"])
    back = contact_row(shop, loser)
    assert back["merged_into"] is None and back["phone"] == "+60123456789"
    assert back["phone_verified_at"] is not None


async def test_unlink_keeps_a_telegram_id_set_on_the_survivor_after_the_link(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    loser = contact(shop, days_old=1, telegram_id=777020)
    out = await link(shop, survivor, loser)
    set_columns(shop, survivor, telegram_id=777021)

    await unlink(shop, out.link_id)

    assert contact_row(shop, survivor)["telegram_id"] == 777021
    back = contact_row(shop, loser)
    assert back["telegram_id"] == 777020 and back["merged_into"] is None


async def test_unlink_leaves_out_a_value_another_contact_took_since(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    loser = contact(
        shop,
        days_old=10,
        phone="+60123456789",
        phone_verified_at="2026-11-01T00:00:00Z",
        telegram_id=777030,
    )
    out = await link(shop, survivor, loser)
    # After the link the survivor changes both, and a third contact proves the same number and
    # takes the Telegram account the loser had.
    set_columns(shop, survivor, phone="+60 19 888 7777", phone_verified_at=None, telegram_id=777031)
    other = contact(
        shop,
        days_old=1,
        phone="0123456789",
        phone_verified_at="2026-11-06T00:00:00Z",
        telegram_id=777030,
    )

    await unlink(shop, out.link_id)

    back = contact_row(shop, loser)
    assert back["merged_into"] is None and back["phone"] == "+60123456789"
    assert back["phone_verified_at"] is None and back["telegram_id"] is None
    o = contact_row(shop, other)
    assert o["phone_verified_at"] is not None and o["telegram_id"] == 777030
    s = contact_row(shop, survivor)
    assert s["phone"] == "+60 19 888 7777" and s["telegram_id"] == 777031
    assert owned(shop, survivor)["contact_identities"] == []


async def test_unlink_of_a_verified_phone_link_takes_back_the_stamp(shop: Shop) -> None:
    staff_entered = contact(shop, days_old=30, phone="012-345 6789")
    newcomer = contact(shop, days_old=0)
    assert (await verify(shop, newcomer, "60123456789")).outcome == "linked"
    assert contact_row(shop, staff_entered)["phone_verified_at"] is not None
    [made] = rows(shop, "SELECT id FROM contact_links WHERE client_id = %s", shop.client)

    await unlink(shop, made["id"])

    s = contact_row(shop, staff_entered)
    assert s["phone"] == "012-345 6789" and s["phone_verified_at"] is None
    assert contact_row(shop, newcomer)["merged_into"] is None


async def test_a_plain_unlink_reports_nothing_skipped(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    loser = contact(
        shop,
        days_old=1,
        phone="+60123456789",
        phone_verified_at="2026-11-01T00:00:00Z",
        telegram_id=777040,
    )
    out = await link(shop, survivor, loser)

    done = await unlink(shop, out.link_id)

    assert done.skipped == []


async def test_an_unlink_names_the_columns_it_left_alone(shop: Shop) -> None:
    survivor = contact(shop, days_old=30)
    loser = contact(
        shop,
        days_old=10,
        phone="+60123456789",
        phone_verified_at="2026-11-01T00:00:00Z",
        telegram_id=777041,
    )
    out = await link(shop, survivor, loser)
    # The survivor's phone and Telegram account change after the link; a third contact proves
    # the same number and takes the Telegram account the loser had.
    set_columns(shop, survivor, phone="+60 19 888 7777", phone_verified_at=None, telegram_id=777042)
    contact(
        shop,
        days_old=1,
        phone="0123456789",
        phone_verified_at="2026-11-06T00:00:00Z",
        telegram_id=777041,
    )

    done = await unlink(shop, out.link_id)

    assert done.skipped == [
        "survivor.phone",
        "survivor.telegram_id",
        "loser.phone_verified_at",
        "loser.telegram_id",
    ]


# ------------------------------------------------------------------------------------------------
# verify_phone


async def test_verify_with_no_other_holder_sets_the_verified_phone(shop: Shop) -> None:
    me = contact(shop, days_old=1)
    out = await verify(shop, me, "012-345 6789")
    assert out.outcome == "set" and out.contact_id == me
    row = contact_row(shop, me)
    assert row["phone"] == "+60123456789" and row["phone_verified_at"] is not None
    stamped = row["phone_verified_at"]

    # The same number again changes nothing.
    assert (await verify(shop, me, "+60 12-345 6789")).outcome == "set"
    assert contact_row(shop, me)["phone_verified_at"] == stamped

    # A different number when one is already verified: no change.
    out = await verify(shop, me, "+60 19 999 9999")
    assert out.outcome == "already" and out.contact_id == me
    assert contact_row(shop, me)["phone"] == "+60123456789"


async def test_verify_with_one_other_holder_links_and_the_survivor_holds_it(shop: Shop) -> None:
    staff_entered = contact(shop, days_old=30, phone="012-345 6789")
    subscribe(shop, staff_entered)
    newcomer = contact(shop, days_old=0)
    identity(shop, newcomer, "whatsapp", "60123456789")

    out = await verify(shop, newcomer, "60123456789")

    assert out.outcome == "linked" and out.contact_id == staff_entered
    s = contact_row(shop, staff_entered)
    assert s["phone_verified_at"] is not None
    assert s["phone"] == "012-345 6789" and s["phone_digits"] == "60123456789"
    assert contact_row(shop, newcomer)["merged_into"] == staff_entered
    [audit] = rows(
        shop, "SELECT reason, created_by FROM contact_links WHERE client_id = %s", shop.client
    )
    assert audit == {"reason": "verified_phone", "created_by": None}
    assert owned(shop, staff_entered)["contact_identities"] == [
        [str(shop.client), "whatsapp", "60123456789"]
    ]


async def test_verify_with_several_holders_opens_one_review_once(shop: Shop) -> None:
    one = contact(shop, days_old=30, phone="0123456789")
    two = contact(shop, days_old=20, phone="+60123456789")
    me = contact(shop, days_old=0)

    first = await verify(shop, me, "60123456789")
    second = await verify(shop, me, "60123456789")

    assert first.outcome == second.outcome == "review" and first.contact_id == me
    assert open_reviews(shop) == [{"contact_a": me, "contact_b": one, "reason": "several_matches"}]
    for c in (one, two, me):
        assert contact_row(shop, c)["merged_into"] is None
        assert contact_row(shop, c)["phone_verified_at"] is None
    assert contact_row(shop, me)["phone"] is None


async def test_verify_never_relinks_a_pair_staff_unlinked(shop: Shop) -> None:
    staff_entered = contact(shop, days_old=30, phone="012-345 6789")
    newcomer = contact(shop, days_old=0)
    assert (await verify(shop, newcomer, "60123456789")).outcome == "linked"
    [made] = rows(shop, "SELECT id FROM contact_links WHERE client_id = %s", shop.client)
    await unlink(shop, made["id"])

    first = await verify(shop, newcomer, "60123456789")
    second = await verify(shop, newcomer, "60123456789")

    assert first.outcome == second.outcome == "review" and first.contact_id == newcomer
    assert open_reviews(shop) == [
        {"contact_a": newcomer, "contact_b": staff_entered, "reason": "previously_unlinked"}
    ]
    for c in (staff_entered, newcomer):
        assert contact_row(shop, c)["merged_into"] is None
    assert contact_row(shop, newcomer)["phone"] is None
    assert contact_row(shop, staff_entered)["phone_verified_at"] is None
    assert len(rows(shop, "SELECT id FROM contact_links WHERE client_id = %s", shop.client)) == 1

    # Staff can still link the same pair, and that closes the review.
    again = await link(shop, newcomer, staff_entered)
    assert again.linked and again.refused is None
    assert open_reviews(shop) == []
    assert again.survivor_id == staff_entered
    assert contact_row(shop, newcomer)["merged_into"] == staff_entered


async def test_a_dismissed_review_does_not_reopen_on_the_next_verify(shop: Shop) -> None:
    holder = contact(shop, days_old=30, phone="0123456789", telegram_id=777008)
    me = contact(shop, days_old=0, telegram_id=777009)
    assert (await verify(shop, me, "60123456789")).outcome == "review"
    [review] = rows(shop, "SELECT id FROM contact_link_reviews WHERE client_id = %s", shop.client)
    async with tx(shop.pool, str(shop.client)) as conn:
        dismissed = await db.q.fetch_one(
            "contacts/review_dismiss.sql",
            {"client_id": shop.client, "review_id": review["id"], "resolved_by": shop.user},
            conn=conn,
        )
    assert dismissed is not None

    # The customer writes again, in either order of the pair: staff's decision sticks.
    assert (await verify(shop, me, "60123456789")).outcome == "review"
    async with tx(shop.pool, str(shop.client)) as conn:
        await db.q.execute(
            "contacts/review_insert.sql",
            {
                "client_id": shop.client,
                "contact_a": holder,
                "contact_b": me,
                "reason": "telegram_conflict",
            },
            conn=conn,
        )

    assert open_reviews(shop) == []
    assert (
        len(rows(shop, "SELECT id FROM contact_link_reviews WHERE client_id = %s", shop.client))
        == 1
    )
    # Another reason for the same pair still opens a review.
    async with tx(shop.pool, str(shop.client)) as conn:
        await db.q.execute(
            "contacts/review_insert.sql",
            {
                "client_id": shop.client,
                "contact_a": me,
                "contact_b": holder,
                "reason": "table_conflict",
            },
            conn=conn,
        )
    assert open_reviews(shop) == [
        {"contact_a": me, "contact_b": holder, "reason": "table_conflict"}
    ]


async def test_verify_with_a_refused_link_opens_a_review_with_that_reason(shop: Shop) -> None:
    holder = contact(shop, days_old=30, phone="0123456789", telegram_id=777006)
    me = contact(shop, days_old=0, telegram_id=777007)

    out = await verify(shop, me, "60123456789")
    again = await verify(shop, me, "60123456789")

    assert out.outcome == again.outcome == "review" and out.contact_id == me
    assert open_reviews(shop) == [
        {"contact_a": me, "contact_b": holder, "reason": "telegram_conflict"}
    ]
    assert contact_row(shop, me)["phone"] is None
    assert contact_row(shop, me)["merged_into"] is None


async def test_verify_with_a_colliding_table_opens_a_table_conflict_review(shop: Shop) -> None:
    holder = contact(shop, days_old=30, phone="0123456789")
    me = contact(shop, days_old=0)
    prepared_request(shop, holder, "dup")
    prepared_request(shop, me, "dup")

    out = await verify(shop, me, "60123456789")

    assert out.outcome == "review"
    assert open_reviews(shop) == [
        {"contact_a": me, "contact_b": holder, "reason": "table_conflict"}
    ]


async def test_verify_an_invalid_phone_writes_nothing(shop: Shop) -> None:
    holder = contact(shop, days_old=30, phone="123")
    me = contact(shop, days_old=0)
    for bad in ("123", "abc", "", "1234567890123456"):
        out = await verify(shop, me, bad)
        assert out.outcome == "invalid" and out.contact_id == me
    assert contact_row(shop, me)["phone"] is None
    assert contact_row(shop, holder)["merged_into"] is None
    assert rows(shop, "SELECT id FROM contact_links WHERE client_id = %s", shop.client) == []
    assert open_reviews(shop) == []


def test_a_typed_number_has_no_entry_point() -> None:
    hints = typing.get_type_hints(verify_phone)
    assert typing.get_args(hints["source"]) == ("whatsapp", "telegram_share", "staff")


# ------------------------------------------------------------------------------------------------
# A link racing an incoming message for the loser


@pytest.mark.parametrize("first", ["link", "message"])
async def test_a_link_racing_a_new_conversation_leaves_nothing_on_the_loser(
    shop: Shop, first: str
) -> None:
    survivor, loser = contact(shop, days_old=9), contact(shop, days_old=1)
    holding = asyncio.Event()

    async def do_link() -> Any:
        if first != "link":
            await holding.wait()
        async with tx(shop.pool, str(shop.client)) as conn:
            out = await link_contacts(
                conn, shop.client, survivor, loser, reason="staff", actor_user_id=shop.user
            )
            if first == "link":
                holding.set()
                await asyncio.sleep(0.3)
        return out

    async def do_message() -> UUID:
        if first != "message":
            await holding.wait()
        async with tx(shop.pool, str(shop.client)) as conn:
            target = await live_contact(conn, shop.client, loser)
            await conn.execute(
                "INSERT INTO conversations (client_id, contact_id, channel, channel_thread)"
                " VALUES (%s, %s, 'whatsapp', %s)",
                (shop.client, target, f"wa-{uuid.uuid4()}"),
            )
            if first == "message":
                holding.set()
                await asyncio.sleep(0.3)
        return target

    out, target = await asyncio.gather(do_link(), do_message())

    assert out.linked
    assert target == (survivor if first == "link" else loser)
    assert owned(shop, loser)["conversations"] == []
    assert len(owned(shop, survivor)["conversations"]) == 1


async def test_live_contact_holds_no_lock_while_it_waits_for_the_lower_one(shop: Shop) -> None:
    low, high = sorted((uuid.uuid4(), uuid.uuid4()))
    survivor, loser = contact(shop, days_old=30, id=low), contact(shop, days_old=1, id=high)
    await link(shop, survivor, loser)
    holding, probe = asyncio.Event(), {}

    async def unlink_side() -> None:
        # An unlink of (survivor, loser) takes the survivor's lock, then the loser's.
        async with tx(shop.pool, str(shop.client)) as conn:
            await conn.execute(LOCK, (f"contact:{low}",))
            holding.set()
            probe["waited"] = await until_waiting(shop)
            cur = await conn.execute(
                "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS ok",
                (f"contact:{high}",),
            )
            row = await cur.fetchone()
            probe["loser_free"] = row is not None and row["ok"]

    async def message_side() -> UUID:
        await holding.wait()
        async with tx(shop.pool, str(shop.client)) as conn:
            return await live_contact(conn, shop.client, loser)

    _, target = await asyncio.gather(unlink_side(), message_side())

    assert probe == {"waited": True, "loser_free": True}
    assert target == survivor


async def test_verify_phone_takes_the_contacts_lock_after_the_phones(shop: Shop) -> None:
    me = contact(shop, days_old=1)
    holding, seen = asyncio.Event(), {}

    async def holder() -> None:
        async with tx(shop.pool, str(shop.client)) as conn:
            await conn.execute(LOCK, (f"contact:{me}",))
            holding.set()
            seen["waited"] = await until_waiting(shop)

    async def verifier() -> Any:
        await holding.wait()
        return await verify(shop, me, "0123456789")

    _, out = await asyncio.gather(holder(), verifier())

    assert seen["waited"] and out.outcome == "set"
    assert contact_row(shop, me)["phone_verified_at"] is not None
