"""One customer across channels: verified phones, linking two contacts, and undoing a link.

A link makes one contact the survivor and moves every contact-owned row of the other (the loser)
to it, so ingress, the inbox and the bot read the survivor without following ``merged_into``. The
loser stays as an empty shell with ``merged_into`` set, and ``contact_links.moved`` records the
primary key of every moved row per table plus both contacts' prior columns, which is what an
unlink (within 30 days) puts back. Rows written after the link stay with the survivor.

Every function runs inside the caller's :func:`csa.core.tenancy.tx`. Lock order, everywhere: the
phone being verified, then the contacts' advisory locks in sorted id order, then their rows. A
caller that both verifies a phone and writes a row for the contact (WhatsApp and Telegram
ingress) calls :func:`verify_phone` first and :func:`live_contact` after it, never the reverse.

Only a channel that proved the number may verify a phone (``source``): a WhatsApp inbound, a
Telegram share of the sender's own number, or a phone staff entered. A number the customer typed
has no entry point here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from fastapi import HTTPException
from psycopg import AsyncConnection, sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from csa.core import db
from csa.core.phone import normalise_my_phone

Conn = AsyncConnection[Any]
Refusal = Literal["telegram_conflict", "same_channel_identity", "table_conflict"]

# Every table whose rows belong to a contact and move with a link (EXCLUDED_TABLES stay where
# they are; tests/test_contact_linking.py checks the two cover every table pointing at a contact).
LINKED_TABLES: tuple[str, ...] = (
    "conversations",
    "orders",
    "order_confirmations",
    "tickets",
    "warranty_claims",
    "pending_actions",
    "subscriptions",
    "bookings",
    "invoices",
    "agent_preparation_requests",
    "signup_requests",
    "contact_identities",
)

# Tables with a contact_id column or a foreign key to contacts whose rows a link does not move.
EXCLUDED_TABLES: tuple[str, ...] = (
    "escalations",  # a record of one turn's handoff; read by conversation_id, which moves
    "quotes",  # legacy baseline table, no code reads or writes it
    "ingress_events",  # the raw inbound audit trail: who the message resolved to when it came in
    "contact_verifications",  # legacy baseline table, no code reads or writes it
    "subscription_requests",  # legacy baseline table, no code reads or writes it
    "console_chat_participants",  # a staff member's own console sandbox contact, never a customer
    "console_chat_threads",  # sandbox threads of that sandbox contact
    "contact_links",  # the link records themselves (survivor and loser)
    "contact_link_reviews",  # reviews name both contacts of a pair as they were
)

# Primary keys other than a single "id" column; the link records such a key as a JSON array.
_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "contact_identities": ("client_id", "channel", "external_id"),
}


@dataclass(frozen=True)
class _Unique:
    """A unique index of a linked table that includes client_id and contact_id."""

    columns: tuple[str, ...]  # the other columns of the index
    where: tuple[tuple[str, str], ...] = ()  # a partial index's predicate: column = value


# The unique indexes that could collide when the loser's rows move to the survivor
# (tests/test_contact_linking.py compares this with the catalog).
_UNIQUES: dict[str, tuple[_Unique, ...]] = {
    "agent_preparation_requests": (_Unique(("agent", "idempotency_key")),),
    "signup_requests": (_Unique(("plan_code",), where=(("status", "pending"),)),),
}


# The columns a link changes on a contact and an unlink puts back.
_CONTACT_COLUMNS = ("phone", "phone_verified_at", "telegram_id")

# merged_into chains are short (a survivor later linked as a loser); this only bounds a cycle.
_MAX_HOPS = 8


@dataclass(frozen=True)
class VerifyResult:
    """What verify_phone did; ``contact_id`` holds the phone now (the survivor after a link)."""

    outcome: Literal["set", "already", "linked", "review", "invalid"]
    contact_id: UUID


@dataclass(frozen=True)
class UnlinkOutcome:
    """An unlink done. ``skipped`` names the contact columns it left alone, as ``role.column``.

    ``role`` is ``survivor`` or ``loser``, ``column`` is ``phone``, ``phone_verified_at`` or
    ``telegram_id``, e.g. ``["loser.phone_verified_at", "loser.telegram_id"]``: a column is listed
    when it does not hold its value from before the link afterwards (changed since the link, or
    taken by another contact). A plain unlink gives ``[]``. Survivor entries come first, then the
    loser's, each in the order phone, phone_verified_at, telegram_id.
    """

    skipped: list[str]


@dataclass(frozen=True)
class LinkOutcome:
    """A link made (``link_id``), or refused with the reason and nothing changed."""

    linked: bool
    link_id: UUID | None
    survivor_id: UUID
    refused: Refusal | None


async def verify_phone(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    phone: str,
    *,
    source: Literal["whatsapp", "telegram_share", "staff"],
) -> VerifyResult:
    """Record a phone the channel ``source`` proved belongs to the contact.

    No other live contact of the company has the same digits: the contact holds the phone,
    verified. Exactly one does (verified or staff-entered): the two are linked and the survivor
    holds it. Several, a refused link, or a pair staff unlinked before (``previously_unlinked``;
    staff can still link it): one open review for the pair and nothing else changes (outcome
    ``review``).
    A contact that already holds a different verified phone keeps it (``already``); a phone that
    does not normalise writes nothing (``invalid``).

    Takes the phone's lock, then the contact's and the other holders' in sorted id order. A
    caller that also writes a row for the contact calls this before :func:`live_contact`, never
    after: a contact lock held before a phone lock reverses the order and can deadlock.
    Tasks 6 and 7: this runs before :func:`live_contact`, and a contact merged by a concurrent
    link raises 409 ``contact_merged``, which the caller must catch and re-resolve.
    """
    del source  # Which channel proved it does not change what happens; the type is the gate.
    normalised = normalise_my_phone(phone)
    if normalised is None:
        return VerifyResult("invalid", contact_id)
    digits = normalised.removeprefix("+")
    await _lock(conn, f"phone:{client_id}:{digits}")
    params = {"client_id": client_id, "contact_id": contact_id, "digits": digits}
    rows = await db.q.fetch_all("contacts/phone_holders.sql", params, conn=conn)
    # The contact and the holders read without a lock, locked in sorted id order, read again.
    for held in sorted({contact_id, *(r["id"] for r in rows)}):
        await _lock(conn, f"contact:{held}")
    rows = await db.q.fetch_all("contacts/phone_holders.sql", params, conn=conn)
    me = next((r for r in rows if r["is_self"]), None)
    if me is None:
        raise HTTPException(404, "contact_not_found")
    if me["merged_into"] is not None:
        raise HTTPException(409, "contact_merged")
    if me["phone_verified_at"] is not None and me["phone_digits"] != digits:
        return VerifyResult("already", contact_id)
    others = [r["id"] for r in rows if not r["is_self"]]
    if not others:
        stamped = await _stamp(conn, client_id, contact_id, normalised)
        return VerifyResult("set" if stamped is not None else "already", contact_id)
    if len(others) > 1:
        await _review(conn, client_id, contact_id, others[0], "several_matches")
        return VerifyResult("review", contact_id)
    undone = await db.q.fetch_one(
        "contacts/link_previously_unlinked.sql",
        {"client_id": client_id, "contact_a": contact_id, "contact_b": others[0]},
        conn=conn,
    )
    if undone is not None and undone["was_unlinked"]:
        await _review(conn, client_id, contact_id, others[0], "previously_unlinked")
        return VerifyResult("review", contact_id)
    out = await link_contacts(
        conn, client_id, contact_id, others[0], reason="verified_phone", actor_user_id=None
    )
    if out.refused is not None:
        await _review(conn, client_id, contact_id, others[0], out.refused)
        return VerifyResult("review", contact_id)
    stamped = await _stamp(conn, client_id, out.survivor_id, normalised)
    if stamped is not None:
        await db.q.execute(
            "contacts/link_linked_survivor.sql",
            {"client_id": client_id, "link_id": out.link_id, "columns": Jsonb(_recorded(stamped))},
            conn=conn,
        )
    return VerifyResult("linked", out.survivor_id)


async def link_contacts(
    conn: Conn,
    client_id: UUID,
    contact_a: UUID,
    contact_b: UUID,
    *,
    reason: Literal["verified_phone", "staff"],
    actor_user_id: UUID | None,
) -> LinkOutcome:
    """Make the two contacts one; the survivor holds a subscription, else is the older.

    Refused, with nothing changed, when both have a Telegram account, when the loser has an
    identity on a channel the survivor already has, or when one of the loser's rows would collide
    with a unique key of the survivor's. 404 ``contact_not_found`` for a contact of another
    company; 409 ``contact_merged`` when either is already a merged shell.
    """
    if contact_a == contact_b:
        raise HTTPException(422, "same_contact")
    pair = await _lock_pair(conn, client_id, contact_a, contact_b)
    if any(row["merged_into"] is not None for row in pair):
        raise HTTPException(409, "contact_merged")
    survivor, loser = sorted(
        pair, key=lambda r: (not r["has_subscription"], r["created_at"], r["id"])
    )
    refused = await _refusal(conn, client_id, survivor, loser)
    if refused is not None:
        return LinkOutcome(False, None, survivor["id"], refused)

    prior = {"survivor": _columns(survivor), "loser": _columns(loser)}
    takes_telegram = survivor["telegram_id"] is None and loser["telegram_id"] is not None
    takes_phone = not (survivor["phone"] or "").strip()
    # The loser leaves the live set (the verified-phone index ignores merged rows) and gives up
    # its Telegram account (unique per company) before the survivor takes either.
    linked = {
        "loser": {
            **prior["loser"],
            "telegram_id": None if takes_telegram else loser["telegram_id"],
        },
        "survivor": {
            "phone": loser["phone"] if takes_phone else survivor["phone"],
            "phone_verified_at": (
                loser["phone_verified_at"] if takes_phone else survivor["phone_verified_at"]
            ),
            "telegram_id": loser["telegram_id"] if takes_telegram else survivor["telegram_id"],
        },
    }
    await _set_contact(conn, client_id, loser["id"], linked["loser"], merged_into=survivor["id"])
    await _set_contact(conn, client_id, survivor["id"], linked["survivor"], merged_into=None)
    moved: dict[str, Any] = {
        table: await _move(conn, table, client_id, loser["id"], survivor["id"])
        for table in LINKED_TABLES
    }
    # Each contact's columns before the link, and as the link left them (an unlink compares).
    moved["contacts"] = {
        **{k: _recorded(v) for k, v in prior.items()},
        "linked": {k: _recorded(v) for k, v in linked.items()},
    }
    link = await db.q.fetch_one(
        "contacts/link_insert.sql",
        {
            "client_id": client_id,
            "survivor_id": survivor["id"],
            "loser_id": loser["id"],
            "reason": reason,
            "moved": Jsonb(moved),
            "created_by": actor_user_id,
        },
        conn=conn,
    )
    await db.q.execute(
        "contacts/review_resolve.sql",
        {
            "client_id": client_id,
            "contact_a": survivor["id"],
            "contact_b": loser["id"],
            "resolution": "linked",
            "resolved_by": actor_user_id,
        },
        conn=conn,
    )
    return LinkOutcome(True, link["id"], survivor["id"], None)


async def unlink_contacts(
    conn: Conn, client_id: UUID, link_id: UUID, actor_user_id: UUID
) -> UnlinkOutcome:
    """Undo a link: every moved row, both identities and both contacts' columns go back.

    A contact's phone (with its stamp) or telegram_id goes back only while it still holds what
    the link left there; a value changed since stays, and a value another contact has taken
    since is left out rather than raise on a unique index. The columns that did not come back
    are returned in ``UnlinkOutcome.skipped`` as ``role.column`` (see :class:`UnlinkOutcome`).

    404 ``link_not_found``; 409 ``link_not_active`` when already undone,
    ``unlink_window_closed`` 30 days after the link, ``survivor_merged`` when the survivor was
    since linked into another, ``later_link_active`` while a later link on either contact is
    active (links are undone newest first).
    """
    link = await db.q.fetch_one(
        "contacts/link_get.sql", {"client_id": client_id, "link_id": link_id}, conn=conn
    )
    if link is None:
        raise HTTPException(404, "link_not_found")
    if link["unlinked_at"] is not None:
        raise HTTPException(409, "link_not_active")
    if link["window_closed"]:
        raise HTTPException(409, "unlink_window_closed")
    survivor_id, loser_id = link["survivor_id"], link["loser_id"]
    recorded = link["moved"]["contacts"]
    # The verified phones this may give back are locked first, as verify_phone does.
    phones = {_phone_key(client_id, recorded[role]) for role in ("survivor", "loser")}
    for key in sorted(k for k in phones if k is not None):
        await _lock(conn, key)
    pair = {row["id"]: row for row in await _lock_pair(conn, client_id, survivor_id, loser_id)}
    if pair[survivor_id]["merged_into"] is not None:
        raise HTTPException(409, "survivor_merged")
    later = await db.q.fetch_one(
        "contacts/link_later_active.sql", {"client_id": client_id, "link_id": link_id}, conn=conn
    )
    if later is not None and later["later_active"]:
        raise HTTPException(409, "later_link_active")
    # The reverse of the link: the survivor gives back first, then the loser is live again.
    skipped: list[str] = []
    for role, contact_id in (("survivor", survivor_id), ("loser", loser_id)):
        now = await _restore(conn, client_id, contact_id, recorded[role], recorded["linked"][role])
        skipped += [f"{role}.{c}" for c in _CONTACT_COLUMNS if now[c] != _prior(recorded[role], c)]
    for table in LINKED_TABLES:
        await _move(
            conn, table, client_id, survivor_id, loser_id, only=link["moved"].get(table, [])
        )
    await db.q.execute(
        "contacts/link_unlinked.sql",
        {"client_id": client_id, "link_id": link_id, "unlinked_by": actor_user_id},
        conn=conn,
    )
    return UnlinkOutcome(skipped)


async def live_contact(conn: Conn, client_id: UUID, contact_id: UUID) -> UUID:
    """Lock the contact as a link does and return the live contact its rows belong to now.

    A writer that resolved a contact earlier (an inbound message) calls this before writing a row
    for it, so a link running at the same time cannot leave that row on a merged shell. Each step
    reads ``merged_into`` without a lock, locks the contact and its target in sorted id order (as
    a link and an unlink do), reads again and follows the chain. A caller that also verifies a
    phone calls :func:`verify_phone` first: a phone lock taken after this one can deadlock.
    """
    current = contact_id
    for _ in range(_MAX_HOPS):
        seen = await _merged_into(conn, client_id, current)
        for held in sorted({current} if seen is None else {current, seen}):
            await _lock(conn, f"contact:{held}")
        now = await _merged_into(conn, client_id, current)
        if now is None:
            return current
        current = now
    raise RuntimeError(f"contact {contact_id} has a merged_into cycle")


async def _merged_into(conn: Conn, client_id: UUID, contact_id: UUID) -> UUID | None:
    row = await db.q.fetch_one(
        "contacts/contact_live.sql", {"client_id": client_id, "contact_id": contact_id}, conn=conn
    )
    if row is None:
        raise HTTPException(404, "contact_not_found")
    return row["merged_into"]


async def _lock(conn: Conn, key: str) -> None:
    await db.q.execute("contacts/contact_lock.sql", {"key": key}, conn=conn)


async def _lock_pair(conn: Conn, client_id: UUID, a: UUID, b: UUID) -> list[dict[str, Any]]:
    for contact_id in sorted((a, b)):
        await _lock(conn, f"contact:{contact_id}")
    rows = await db.q.fetch_all(
        "contacts/contact_rows.sql", {"client_id": client_id, "ids": [a, b]}, conn=conn
    )
    if len(rows) != 2:
        raise HTTPException(404, "contact_not_found")
    return rows


async def _refusal(
    conn: Conn, client_id: UUID, survivor: dict[str, Any], loser: dict[str, Any]
) -> Refusal | None:
    """Why the pair cannot be linked as is, checked before anything moves."""
    if survivor["telegram_id"] is not None and loser["telegram_id"] is not None:
        return "telegram_conflict"
    channels = await db.q.fetch_all(
        "contacts/identity_channels.sql",
        {"client_id": client_id, "ids": [survivor["id"], loser["id"]]},
        conn=conn,
    )
    held = {r["channel"] for r in channels if r["contact_id"] == survivor["id"]}
    if any(r["channel"] in held for r in channels if r["contact_id"] == loser["id"]):
        return "same_channel_identity"
    params = {"client_id": client_id, "survivor": survivor["id"], "loser": loser["id"]}
    for table, uniques in _UNIQUES.items():
        for unique in uniques:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(_collision_sql(table, unique), params)
                hit = await cur.fetchone()
            if hit is not None and hit["hit"]:
                return "table_conflict"
    return None


async def _move(
    conn: Conn,
    table: str,
    client_id: UUID,
    source: UUID,
    target: UUID,
    *,
    only: list[Any] | None = None,
) -> list[Any]:
    """Re-point the source's rows of one table to the target (only the given keys, if any)."""
    params = {
        "client_id": client_id,
        "source": source,
        "target": target,
        "only": None if only is None else Jsonb(only),
    }
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_move_sql(table), params)
        return [row["pk"] for row in await cur.fetchall()]


async def _set_contact(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    columns: dict[str, Any],
    *,
    merged_into: UUID | None,
) -> None:
    await db.q.execute(
        "contacts/contact_set.sql",
        {
            "client_id": client_id,
            "id": contact_id,
            "phone": columns["phone"],
            "phone_verified_at": columns["phone_verified_at"],
            "telegram_id": columns["telegram_id"],
            "merged_into": merged_into,
        },
        conn=conn,
    )


async def _restore(
    conn: Conn,
    client_id: UUID,
    contact_id: UUID,
    prior: dict[str, Any],
    linked: dict[str, Any],
) -> dict[str, Any]:
    """Put back a contact's prior columns where they still hold what the link left there.

    Returns the contact's three columns as they are afterwards.
    """
    row = await db.q.fetch_one(
        "contacts/contact_restore.sql",
        {
            "client_id": client_id,
            "id": contact_id,
            **{k: prior[k] for k in _CONTACT_COLUMNS},
            **{f"linked_{k}": linked[k] for k in _CONTACT_COLUMNS},
        },
        conn=conn,
    )
    assert row is not None  # _lock_pair found the contact
    return row


async def _stamp(
    conn: Conn, client_id: UUID, contact_id: UUID, phone: str
) -> dict[str, Any] | None:
    """Stamp the verified phone; the contact's columns after it, or None when left alone."""
    return await db.q.fetch_one(
        "contacts/verify_phone_set.sql",
        {"client_id": client_id, "contact_id": contact_id, "phone": phone},
        conn=conn,
    )


async def _review(conn: Conn, client_id: UUID, a: UUID, b: UUID, reason: str) -> None:
    await db.q.execute(
        "contacts/review_insert.sql",
        {"client_id": client_id, "contact_a": a, "contact_b": b, "reason": reason},
        conn=conn,
    )


def _columns(row: dict[str, Any]) -> dict[str, Any]:
    return {k: row[k] for k in _CONTACT_COLUMNS}


def _prior(recorded: dict[str, Any], column: str) -> Any:
    """A recorded column value as the database returns it (the stamp back from ISO text)."""
    value = recorded[column]
    if column == "phone_verified_at" and value is not None:
        return datetime.fromisoformat(value)
    return value


def _recorded(columns: dict[str, Any]) -> dict[str, Any]:
    """The contact columns as contact_links.moved stores them (the stamp as ISO text)."""
    return {**_columns(columns), "phone_verified_at": _iso(columns)}


def _phone_key(client_id: UUID, columns: dict[str, Any]) -> str | None:
    """The verify_phone lock key of a recorded phone that was verified, else None."""
    if columns["phone_verified_at"] is None or not columns["phone"]:
        return None
    normalised = normalise_my_phone(columns["phone"])
    return None if normalised is None else f"phone:{client_id}:{normalised.removeprefix('+')}"


def _iso(columns: dict[str, Any]) -> str | None:
    stamp: datetime | None = columns["phone_verified_at"]
    return None if stamp is None else stamp.isoformat()


def _pk(table: str) -> sql.Composable:
    """The row's primary key as jsonb: the id itself, or an array of a composite key's values."""
    columns = _PRIMARY_KEYS.get(table, ("id",))
    if len(columns) == 1:
        return sql.SQL("to_jsonb({})").format(sql.Identifier(columns[0]))
    return sql.SQL("jsonb_build_array({})").format(
        sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    )


def _move_sql(table: str) -> sql.Composed:
    """UPDATE re-pointing one linked table; table and column names come from the constants."""
    if table not in LINKED_TABLES:
        raise ValueError(f"not a linked table: {table!r}")
    return sql.SQL(
        "UPDATE {table} SET contact_id = %(target)s"
        " WHERE client_id = %(client_id)s AND contact_id = %(source)s"
        " AND (%(only)s::jsonb IS NULL OR {pk} IN (SELECT jsonb_array_elements(%(only)s::jsonb)))"
        " RETURNING {pk} AS pk"
    ).format(table=sql.Identifier("public", table), pk=_pk(table))


def _collision_sql(table: str, unique: _Unique) -> sql.Composed:
    """SELECT whether a loser row and a survivor row share the values of one unique index."""
    same: list[sql.Composable] = [
        sql.SQL("s.{c} = l.{c}").format(c=sql.Identifier(c)) for c in unique.columns
    ]
    for column, value in unique.where:
        same.append(
            sql.SQL("l.{c} = {v} AND s.{c} = {v}").format(
                c=sql.Identifier(column), v=sql.Literal(value)
            )
        )
    return sql.SQL(
        "SELECT EXISTS (SELECT 1 FROM {table} l JOIN {table} s"
        " ON s.client_id = l.client_id AND s.contact_id = %(survivor)s AND {same}"
        " WHERE l.client_id = %(client_id)s AND l.contact_id = %(loser)s) AS hit"
    ).format(table=sql.Identifier("public", table), same=sql.SQL(" AND ").join(same))
