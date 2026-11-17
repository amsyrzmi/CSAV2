"""The Inbox queue at 50,000 conversations for one company (plan 1a, task 9; design 6.5).

One company holds 50,000 synthetic conversations and contacts made with ``generate_series`` (800
needs us, of which 100 overdue and 60 the asking person's; 300 waiting on the customer, 240 of
them the asking person's; 200 snoozed; 2,000 resolved; the rest with the bot), a few hundred
contacts with an order, and a second small company that must never show. The queue columns are
written directly as the owner (the triggers react to messages and escalations only).

Then, as ``csa_app`` with the company's tenant setting (as the API runs), every tab's real query,
with the parameters the list module itself builds, is explained in JSON and checked: the tab's
index is used, nothing is sorted (``order=priority`` sorts the needs_staff rows only, read through
a needs_staff partial index), ``conversations`` is never read whole. Each query also runs 15 times
on one connection, so psycopg prepares it, and the prepared statement must still be planned for
its own values (no generic plan). Pages and counts are compared with a brute force read as the
owner. Plans and results are asserted, never time; the search times are printed (``-s``).
"""

from __future__ import annotations

import re
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, LiteralString, cast
from uuid import UUID, uuid4

import psycopg
import pytest
from csa.console.inbox_contact import parse_search
from csa.console.inbox_cursor import decode
from csa.console.inbox_queue_list import (
    COUNTS_QUERY,
    PRIORITY_RANK,
    QueueFilter,
    custom_plans,
    fetch_counts,
    fetch_page,
    page_query,
)
from csa.core import db
from csa.core.tenancy import tx
from psycopg import sql
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner

pytestmark = [requires_docker, pytest.mark.asyncio, pytest.mark.slow]

TOTAL = 50_000
LIMIT = 20
RUNS = 15
NEEDS_STAFF_INDEXES = frozenset({"conversations_queue_due_idx", "conversations_queue_since_idx"})
NEIGHBOUR_CONTACTS = 150_000


@dataclass(frozen=True)
class Layout:
    """Which generated rows (1 to n) get which queue state; the rest stay with the bot."""

    n: int
    needs: int  # 1..needs need us
    overdue: int  # 1..overdue of them are overdue
    mine_needs: tuple[int, int]  # these needs-us rows are the asking person's
    colleague_needs: tuple[int, int]  # and these a colleague's
    waiting: int  # needs+1..waiting wait on the customer
    mine_waiting: int  # needs+1..mine_waiting of them are the asking person's
    snoozed: int  # waiting+1..snoozed are snoozed
    resolved: int  # snoozed+1..resolved are resolved


BIG = Layout(TOTAL, 800, 100, (101, 160), (161, 200), 1100, 1040, 1300, 3300)
SMALL = Layout(500, 60, 10, (11, 30), (31, 35), 80, 75, 90, 150)


@dataclass(frozen=True)
class World:
    client_id: UUID
    other_id: UUID
    me: UUID
    colleague: UUID


SEED_CONTACTS: LiteralString = """
INSERT INTO public.contacts (id, client_id, full_name, telegram_username, phone)
SELECT md5('scale-contact-' || %(client)s::text || '-' || i)::uuid, %(client)s,
       'Synthetic Person ' || i,
       CASE WHEN i %% 3 = 0 THEN 'synthetic_user_' || i END,
       CASE WHEN i <= 10000 THEN '+60 12-555 ' || lpad((i - 1)::text, 4, '0') END
FROM generate_series(1, %(n)s) AS i
"""
SEED_CONVERSATIONS: LiteralString = """
INSERT INTO public.conversations
    (id, client_id, contact_id, channel, channel_thread, opened_at, last_activity_at)
SELECT md5('scale-conversation-' || %(client)s::text || '-' || i)::uuid, %(client)s,
       md5('scale-contact-' || %(client)s::text || '-' || i)::uuid,
       CASE WHEN i %% 2 = 0 THEN 'telegram' ELSE 'whatsapp' END,
       'scale-thread-' || %(client)s::text || '-' || i,
       now() - interval '91 days',
       -- 7919 is prime to the 129,600 minutes of 90 days: every row its own time.
       now() - ((i * 7919) %% 129600) * interval '1 minute'
FROM generate_series(1, %(n)s) AS i
"""
# One outbound assistant message per queued row (and a few bot rows) at the row's activity time,
# so the last-message lateral has data and the message trigger leaves the activity time alone.
SEED_MESSAGES: LiteralString = """
INSERT INTO public.messages
    (client_id, conversation_id, direction, author, body, redacted_body, idempotency_key,
     created_at)
SELECT c.client_id, c.id, 'outbound', 'assistant', 'Synthetic reply', 'Synthetic reply',
       'scale-' || c.id, c.last_activity_at
FROM generate_series(1, %(upto)s) AS i
JOIN public.conversations c
  ON c.id = md5('scale-conversation-' || %(client)s::text || '-' || i)::uuid
"""
SEED_ORDERS: LiteralString = """
INSERT INTO public.orders (id, client_id, contact_id, status, total)
SELECT 'SCALE-' || %(client)s::text || '-' || i, %(client)s,
       md5('scale-contact-' || %(client)s::text || '-' || i)::uuid, 'confirmed', 10
FROM generate_series(1, %(n)s) AS i
WHERE i %% 125 = 7
"""
SEED_QUEUE: LiteralString = """
UPDATE public.conversations c
SET queue_status = s.state,
    queue_reason = CASE WHEN s.state = 'needs_staff' THEN
        (ARRAY['escalation', 'customer_message', 'held_reply'])[s.i %% 3 + 1] END,
    priority = CASE WHEN s.state = 'needs_staff' THEN
        (ARRAY['urgent', 'high', 'high', 'normal', 'normal', 'normal', 'normal', 'normal',
               'normal', 'low'])[s.i %% 10 + 1] ELSE 'normal' END,
    topic = (ARRAY['purchase', 'question', NULL, NULL])[s.i %% 4 + 1],
    assignee_user_id = s.assignee,
    claimed_at = CASE WHEN s.assignee IS NOT NULL THEN now() END,
    needs_staff_since = s.since,
    due_at = s.since + interval '240 minutes',
    snoozed_until = CASE WHEN s.state = 'snoozed' THEN now() + (s.i %% 48 + 1) * interval '1 hour'
                    END,
    resolved_at = CASE WHEN s.state = 'resolved' THEN now() - (s.i %% 1000) * interval '1 minute'
                  END,
    last_staff_reply_at = CASE WHEN s.state = 'waiting_customer'
                          THEN now() - (s.i %% 500) * interval '1 minute' END
FROM (
    SELECT g.i, md5('scale-conversation-' || %(client)s::text || '-' || g.i)::uuid AS id, g.state,
           CASE WHEN g.state = 'needs_staff' AND g.i <= %(overdue)s
                THEN now() - (300 + g.i) * interval '1 minute'
                WHEN g.state = 'needs_staff' THEN now() - ((g.i * 37) %% 200) * interval '1 minute'
           END AS since,
           CASE WHEN g.state = 'needs_staff' AND g.i BETWEEN %(mine_from)s AND %(mine_to)s
                THEN %(me)s::uuid
                WHEN g.state = 'needs_staff' AND g.i BETWEEN %(colleague_from)s AND %(colleague_to)s
                THEN %(colleague)s::uuid
                WHEN g.state = 'waiting_customer' AND g.i <= %(mine_waiting)s THEN %(me)s::uuid
                WHEN g.state = 'snoozed' AND g.i %% 4 = 0 THEN %(me)s::uuid
           END AS assignee
    FROM (
        SELECT i, CASE WHEN i <= %(needs)s THEN 'needs_staff'
                       WHEN i <= %(waiting)s THEN 'waiting_customer'
                       WHEN i <= %(snoozed)s THEN 'snoozed'
                       ELSE 'resolved' END AS state
        FROM generate_series(1, %(resolved)s) AS i
    ) g
) s
WHERE c.id = s.id
"""


def _seed(
    conn: psycopg.Connection[Any], client: UUID, me: UUID, colleague: UUID, at: Layout
) -> None:
    base = {"client": client, "n": at.n}
    conn.execute(SEED_CONTACTS, base)
    conn.execute(SEED_CONVERSATIONS, base)
    conn.execute(SEED_MESSAGES, {**base, "upto": at.resolved + 200})
    conn.execute(SEED_ORDERS, base)
    conn.execute(
        SEED_QUEUE,
        {
            "client": client,
            "me": me,
            "colleague": colleague,
            "needs": at.needs,
            "overdue": at.overdue,
            "mine_from": at.mine_needs[0],
            "mine_to": at.mine_needs[1],
            "colleague_from": at.colleague_needs[0],
            "colleague_to": at.colleague_needs[1],
            "waiting": at.waiting,
            "mine_waiting": at.mine_waiting,
            "snoozed": at.snoozed,
            "resolved": at.resolved,
        },
    )


@pytest.fixture(scope="module")
def world(migrated_dsn: str) -> World:
    w = World(uuid4(), uuid4(), uuid4(), uuid4())
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        for client in (w.client_id, w.other_id):
            conn.execute(
                "INSERT INTO clients (id, slug, name, platform_version)"
                " VALUES (%s, %s, 'Synthetic Co', 'test')",
                (client, f"scale-{client}"),
            )
        for user in (w.me, w.colleague):
            conn.execute(
                "INSERT INTO console_users (id, email, display_name)"
                " VALUES (%s, %s, 'Synthetic Member')",
                (user, f"{user}@example.test"),
            )
        _seed(conn, w.client_id, w.me, w.colleague, BIG)
        _seed(conn, w.other_id, w.me, w.colleague, SMALL)
        # Other companies' contacts, as a shared database holds: the company's own contacts are
        # then a fraction of the table and a search reads them through a client_id index.
        neighbour = uuid4()
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version)"
            " VALUES (%s, %s, 'Synthetic Co', 'test')",
            (neighbour, f"scale-{neighbour}"),
        )
        conn.execute(SEED_CONTACTS, {"client": neighbour, "n": NEIGHBOUR_CONTACTS})
        conn.execute(
            "ANALYZE public.contacts, public.conversations, public.messages, public.orders"
        )
    return w


@pytest.fixture
async def app_pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(app_dsn, min_size=1, max_size=2, autocommit=True)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


# --- the brute force, read as the owner -----------------------------------------------------------


@dataclass
class Rows:
    """Company A's conversations as the owner reads them, and its known contacts."""

    rows: list[dict[str, Any]]
    known: set[UUID] = field(default_factory=set)
    other: set[UUID] = field(default_factory=set)


@pytest.fixture(scope="module")
def brute(migrated_dsn: str, world: World) -> Rows:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT id, contact_id, queue_status, assignee_user_id, due_at, needs_staff_since,"
            " priority, last_activity_at FROM conversations WHERE client_id = %s",
            (world.client_id,),
        ).fetchall()
        known = conn.execute(
            "SELECT DISTINCT contact_id FROM orders WHERE client_id = %s", (world.client_id,)
        ).fetchall()
        other = conn.execute(
            "SELECT id FROM conversations WHERE client_id = %s", (world.other_id,)
        ).fetchall()
    return Rows(rows, {r["contact_id"] for r in known}, {r["id"] for r in other})


FAR = datetime.max.replace(tzinfo=UTC)


def _by_activity(rows: list[dict[str, Any]]) -> list[UUID]:
    ordered = sorted(rows, key=lambda r: (r["last_activity_at"], r["id"]), reverse=True)
    return [r["id"] for r in ordered]


def _by_due(rows: list[dict[str, Any]]) -> list[UUID]:
    def key(r: dict[str, Any]) -> Any:
        return (r["due_at"] is None, r["due_at"] or FAR, r["id"])

    return [r["id"] for r in sorted(rows, key=key)]


def _by_waiting(rows: list[dict[str, Any]]) -> list[UUID]:
    return [r["id"] for r in sorted(rows, key=lambda r: (r["needs_staff_since"], r["id"]))]


def _by_priority(rows: list[dict[str, Any]]) -> list[UUID]:
    def key(r: dict[str, Any]) -> Any:
        return (-PRIORITY_RANK[r["priority"]], r["due_at"] is None, r["due_at"] or FAR, r["id"])

    return [r["id"] for r in sorted(rows, key=key)]


# --- the tabs -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tab:
    label: str
    query: dict[str, Any]
    index: str
    expected: Callable[[World, Rows], list[UUID]]
    sorts: bool = False  # order=priority: a sort of the needs_staff rows only


def _rows(b: Rows, keep: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
    return [r for r in b.rows if keep(r)]


def _needs(r: dict[str, Any]) -> bool:
    return r["queue_status"] == "needs_staff"


TABS: tuple[Tab, ...] = (
    Tab(
        "needs us",
        {"state": ["needs_staff"]},
        "conversations_queue_due_idx",
        lambda w, b: _by_due(_rows(b, _needs)),
    ),
    Tab(
        "mine",
        {"state": ["needs_staff", "waiting_customer"], "assignee": "me", "order": "due"},
        "conversations_queue_mine_idx",
        lambda w, b: _by_due(
            _rows(
                b,
                lambda r: (
                    r["assignee_user_id"] == w.me
                    and r["queue_status"] in {"needs_staff", "waiting_customer"}
                ),
            )
        ),
    ),
    Tab(
        "unassigned",
        {"state": ["needs_staff"], "assignee": "none"},
        "conversations_queue_due_idx",
        lambda w, b: _by_due(_rows(b, lambda r: _needs(r) and r["assignee_user_id"] is None)),
    ),
    Tab(
        "overdue",
        {"state": ["needs_staff"], "overdue": True},
        "conversations_queue_due_idx",
        lambda w, b: _by_due(_rows(b, lambda r: _needs(r) and r["due_at"] < datetime.now(UTC))),
    ),
    Tab(
        "waiting on customer",
        {"state": ["waiting_customer"]},
        "conversations_queue_activity_idx",
        lambda w, b: _by_activity(_rows(b, lambda r: r["queue_status"] == "waiting_customer")),
    ),
    Tab(
        "snoozed",
        {"state": ["snoozed"]},
        "conversations_queue_activity_idx",
        lambda w, b: _by_activity(_rows(b, lambda r: r["queue_status"] == "snoozed")),
    ),
    Tab(
        "resolved",
        {"state": ["resolved"]},
        "conversations_activity_idx",
        lambda w, b: _by_activity(_rows(b, lambda r: r["queue_status"] == "resolved")),
    ),
    Tab("all", {}, "conversations_activity_idx", lambda w, b: _by_activity(b.rows)),
    Tab(
        "needs us, longest waiting",
        {"state": ["needs_staff"], "order": "waiting"},
        "conversations_queue_since_idx",
        lambda w, b: _by_waiting(_rows(b, _needs)),
    ),
    Tab(
        "needs us, by priority",
        {"state": ["needs_staff"], "order": "priority"},
        "a needs_staff partial index",
        lambda w, b: _by_priority(_rows(b, _needs)),
        sorts=True,
    ),
)


# --- plans ----------------------------------------------------------------------------------------


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", []):
        yield from _nodes(child)


def _summary(plan: dict[str, Any]) -> str:
    """One line per node: type, relation and index, for the report and assertion messages."""
    lines = []
    for node in _nodes(plan):
        parts = [node["Node Type"], node.get("Relation Name", ""), node.get("Index Name", "")]
        lines.append(" ".join(p for p in parts if p))
    return " > ".join(lines)


def _tree(plan: dict[str, Any], depth: int = 0) -> str:
    """The plan as an indented tree with joins and conditions, printed for the report (``-s``)."""
    keys = ("Join Type", "Relation Name", "Index Name", "Index Cond", "Filter", "Sort Key")
    detail = "; ".join(f"{k}: {plan[k]}" for k in keys if k in plan)
    lines = [f"{'  ' * depth}{plan['Node Type']} [{detail}]"]
    lines += [_tree(child, depth + 1) for child in plan.get("Plans", [])]
    return "\n".join(lines)


def _on_conversations(node: dict[str, Any]) -> bool:
    return node.get("Relation Name") == "conversations"


async def _explain(conn: Any, path: str, params: dict[str, Any]) -> dict[str, Any]:
    text = cast(LiteralString, "EXPLAIN (FORMAT JSON) " + db.q.load(path))
    row = await (await conn.execute(text, params)).fetchone()
    return row["QUERY PLAN"][0]["Plan"]


def _check(tab: Tab, plan: dict[str, Any], *, seek: bool) -> None:
    nodes = list(_nodes(plan))
    where = (tab.label, _summary(plan))
    used = {n.get("Index Name") for n in nodes}
    assert not [n for n in nodes if n["Node Type"] == "Seq Scan" and _on_conversations(n)], where
    if tab.sorts:
        assert used & NEEDS_STAFF_INDEXES, where
    else:
        assert tab.index in used, where
    sorts = [n for n in nodes if "Sort" in n["Node Type"]]
    if not tab.sorts:
        assert not sorts, where
    else:
        assert sorts, where
        for sort in sorts:
            scans = [n for n in _nodes(sort) if _on_conversations(n) or "Index Name" in n]
            conversation_scans = [n for n in scans if n.get("Index Name", "").startswith("conv")]
            assert conversation_scans, where
            for scan in conversation_scans:
                assert scan["Index Name"] in NEEDS_STAFF_INDEXES, where
    if seek:
        # The cursor is an index condition (a seek), never a filter over the rows it skips.
        served = [n for n in nodes if n.get("Index Name") == tab.index]
        assert any("ROW(" in n.get("Index Cond", "") for n in served), where
        assert not [n for n in nodes if "ROW(" in n.get("Filter", "")], where


def _filter(tab: Tab) -> QueueFilter:
    return QueueFilter.model_validate(tab.query)


async def _page_and_plan(
    conn: Any, w: World, tab: Tab, cursor_token: str | None
) -> tuple[list[UUID], str | None, dict[str, Any]]:
    flt = _filter(tab)
    cursor = decode(cursor_token, flt.effective_order()) if cursor_token else None
    rows, next_cursor = await fetch_page(
        conn, flt, client_id=w.client_id, user_id=w.me, search=None, cursor=cursor, limit=LIMIT
    )
    path, params = page_query(
        flt, client_id=w.client_id, user_id=w.me, search=None, cursor=cursor, limit=LIMIT
    )
    await custom_plans(conn)
    return [r["id"] for r in rows], next_cursor, await _explain(conn, path, params)


@pytest.mark.parametrize("tab", TABS, ids=[t.label for t in TABS])
async def test_each_tab_reads_its_index_without_a_sort_on_both_pages(
    world: World, brute: Rows, app_pool: AsyncConnectionPool, tab: Tab
) -> None:
    expected = tab.expected(world, brute)
    assert len(expected) > 2 * LIMIT, tab.label
    async with tx(app_pool, str(world.client_id)) as conn:
        first, token, plan = await _page_and_plan(conn, world, tab, None)
        print(f"\n[{tab.label}] first page: {_summary(plan)}")
        _check(tab, plan, seek=False)
        assert first == expected[:LIMIT], tab.label
        assert token is not None
        second, _, plan = await _page_and_plan(conn, world, tab, token)
        print(f"[{tab.label}] cursor page:\n{_tree(plan)}")
        order = _filter(tab).effective_order()
        # The priority order sorts, so it has no seek; the others seek when every row in scope
        # has the sort time (activity always does).
        seek = order == "activity" or (order != "priority" and _filter(tab).needs_staff_only())
        _check(tab, plan, seek=seek)
        assert second == expected[LIMIT : 2 * LIMIT], tab.label


# --- prepared statements --------------------------------------------------------------------------


def _names_in_order(text: str) -> list[str]:
    """The named parameters in the order psycopg numbers them ($1, $2, ...): first appearance."""
    seen: list[str] = []
    for name in re.findall(r"%\((\w+)\)s", text):
        if name not in seen:
            seen.append(name)
    return seen


async def _prepared(conn: Any, path: str) -> list[dict[str, Any]]:
    first_line = db.q.load(path).splitlines()[0]
    cursor = await conn.execute(
        "SELECT name, parameter_types::text[] AS types, generic_plans, custom_plans"
        " FROM pg_prepared_statements WHERE statement LIKE %s",
        (first_line.replace("%", r"\%").replace("_", r"\_") + "%",),
    )
    return await cursor.fetchall()


async def _explain_execute(
    conn: Any, statement: dict[str, Any], path: str, params: dict[str, Any]
) -> dict[str, Any]:
    names = _names_in_order(db.q.load(path))
    assert len(names) == len(statement["types"]), (names, statement["types"])
    query = sql.SQL("EXPLAIN (FORMAT JSON) EXECUTE {}({})").format(
        sql.Identifier(statement["name"]),
        sql.SQL(", ").join(sql.Literal(params[name]) for name in names),
    )
    row = await (await conn.execute(query)).fetchone()
    return row["QUERY PLAN"][0]["Plan"]


async def test_prepared_statements_keep_custom_plans_after_many_runs(
    world: World, app_pool: AsyncConnectionPool
) -> None:
    """psycopg prepares a query after five runs on a connection; PostgreSQL may then pick a
    generic plan, where `$n IS NULL OR ...` is not folded. Every tab runs 15 times on ONE
    connection, and each prepared statement must show only custom plans and still read the tab's
    index."""
    async with app_pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.client_id', %s, true)", (str(world.client_id),)
            )
            for tab in TABS:
                for _ in range(RUNS):
                    await fetch_page(
                        conn,
                        _filter(tab),
                        client_id=world.client_id,
                        user_id=world.me,
                        search=None,
                        cursor=None,
                        limit=LIMIT,
                    )
            for _ in range(RUNS):
                await fetch_counts(conn, world.client_id, world.me)
            for tab in TABS:
                flt = _filter(tab)
                path, params = page_query(
                    flt,
                    client_id=world.client_id,
                    user_id=world.me,
                    search=None,
                    cursor=None,
                    limit=LIMIT,
                )
                statements = await _prepared(conn, path)
                assert statements, (tab.label, "the list query was never prepared")
                for statement in statements:
                    assert statement["generic_plans"] == 0, (tab.label, statement)
                    assert statement["custom_plans"] >= RUNS - 5, (tab.label, statement)
                matching = [s for s in statements if s["custom_plans"] > 0]
                plan = await _explain_execute(conn, matching[-1], path, params)
                print(f"\n[{tab.label}] prepared, after {RUNS} runs: {_summary(plan)}")
                _check(tab, plan, seek=False)
            counts = await _prepared(conn, COUNTS_QUERY)
            assert counts and all(s["generic_plans"] == 0 for s in counts), counts
            # For the report only: what a generic plan of the same statements would read.
            await conn.execute("SET LOCAL plan_cache_mode = force_generic_plan")
            for tab in TABS:
                path, params = page_query(
                    _filter(tab),
                    client_id=world.client_id,
                    user_id=world.me,
                    search=None,
                    cursor=None,
                    limit=LIMIT,
                )
                statement = [s for s in await _prepared(conn, path) if s["custom_plans"]][-1]
                plan = await _explain_execute(conn, statement, path, params)
                print(f"\n[{tab.label}] generic plan (not used): {_summary(plan)}")


# --- search, sender and counts --------------------------------------------------------------------


SEARCHES = {
    # A term with digits also matches phone numbers holding them (the list's rule).
    "name": ("Person 4242", "full_name ILIKE '%%person 4242%%' OR k.phone LIKE '%%4242'"),
    "username": (
        "@synthetic_user_4242",
        "telegram_username ILIKE '%%synthetic\\_user\\_4242%%' OR k.phone LIKE '%%4242'",
    ),
    "phone digits": ("0125550148", "phone = '+60 12-555 0148'"),
}


async def test_search_returns_a_first_page_without_reading_conversations_whole(
    migrated_dsn: str, world: World, app_pool: AsyncConnectionPool
) -> None:
    with _owner(migrated_dsn) as owner:
        indexes = {
            r["indexname"]: r["indexdef"]
            for r in owner.execute(
                "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'contacts'"
            ).fetchall()
        }
    client_btrees = {name for name, ddl in indexes.items() if "USING btree (client_id" in ddl}
    assert client_btrees, indexes
    for label, (q, owner_match) in SEARCHES.items():
        with _owner(migrated_dsn) as owner:
            expected = {
                r["id"]
                for r in owner.execute(
                    cast(
                        LiteralString,
                        "SELECT c.id FROM conversations c JOIN contacts k ON k.id = c.contact_id"
                        " WHERE c.client_id = %s AND (k." + owner_match + ")",
                    ),
                    (world.client_id,),
                ).fetchall()
            }
        assert 0 < len(expected) <= LIMIT, (label, len(expected))
        flt = QueueFilter(q=q)
        search = parse_search(q)
        async with tx(app_pool, str(world.client_id)) as conn:
            started = time.perf_counter()
            rows, _ = await fetch_page(
                conn,
                flt,
                client_id=world.client_id,
                user_id=world.me,
                search=search,
                cursor=None,
                limit=LIMIT,
            )
            elapsed = (time.perf_counter() - started) * 1000
            path, params = page_query(
                flt,
                client_id=world.client_id,
                user_id=world.me,
                search=search,
                cursor=None,
                limit=LIMIT,
            )
            plan = await _explain(conn, path, params)
        print(f"\n[search {label}] {len(rows)} rows in {elapsed:.1f} ms:\n{_tree(plan)}")
        assert {r["id"] for r in rows} == expected, label
        nodes = list(_nodes(plan))
        assert not [n for n in nodes if n["Node Type"] == "Seq Scan" and _on_conversations(n)]
        contact_scans = [n for n in nodes if n.get("Relation Name") == "contacts"]
        assert contact_scans, (label, _summary(plan))
        assert not [n for n in contact_scans if n["Node Type"] == "Seq Scan"], (
            label,
            _summary(plan),
        )
        assert {n.get("Index Name") for n in nodes} & client_btrees, (label, _summary(plan))


async def test_sender_known_on_all_returns_a_first_page(
    world: World, brute: Rows, app_pool: AsyncConnectionPool
) -> None:
    flt = QueueFilter(sender="known")
    known = [r for r in brute.rows if r["contact_id"] in brute.known]
    async with tx(app_pool, str(world.client_id)) as conn:
        rows, token = await fetch_page(
            conn,
            flt,
            client_id=world.client_id,
            user_id=world.me,
            search=None,
            cursor=None,
            limit=LIMIT,
        )
        path, params = page_query(
            flt,
            client_id=world.client_id,
            user_id=world.me,
            search=None,
            cursor=None,
            limit=LIMIT,
        )
        plan = await _explain(conn, path, params)
    print(f"\n[sender=known] {_summary(plan)}")
    assert [r["id"] for r in rows] == _by_activity(known)[:LIMIT]
    assert token is not None
    nodes = list(_nodes(plan))
    assert "conversations_activity_idx" in {n.get("Index Name") for n in nodes}
    assert not [n for n in nodes if n["Node Type"] == "Seq Scan" and _on_conversations(n)]
    assert not [n for n in nodes if "Sort" in n["Node Type"]], _summary(plan)


BRUTE_COUNTS: LiteralString = """
SELECT count(*) FILTER (WHERE queue_status = 'needs_staff')::int AS needs_us,
       count(*) FILTER (WHERE assignee_user_id = %(me)s
                          AND queue_status IN ('needs_staff', 'waiting_customer'))::int AS mine,
       count(*) FILTER (WHERE queue_status = 'needs_staff'
                          AND assignee_user_id IS NULL)::int AS unassigned,
       count(*) FILTER (WHERE queue_status = 'needs_staff' AND due_at < now())::int AS overdue,
       count(*) FILTER (WHERE queue_status = 'waiting_customer')::int AS waiting_customer,
       count(*) FILTER (WHERE queue_status = 'snoozed')::int AS snoozed,
       min(needs_staff_since) FILTER (WHERE queue_status = 'needs_staff') AS oldest_waiting_at
FROM conversations WHERE client_id = %(client)s
"""


async def test_counts_equal_a_brute_force_count_and_read_the_partial_indexes(
    migrated_dsn: str, world: World, app_pool: AsyncConnectionPool
) -> None:
    with _owner(migrated_dsn) as owner:
        expected = owner.execute(
            BRUTE_COUNTS, {"me": world.me, "client": world.client_id}
        ).fetchone()
        other = owner.execute(BRUTE_COUNTS, {"me": world.me, "client": world.other_id}).fetchone()
    assert expected is not None and other is not None
    assert expected["needs_us"] == BIG.needs and expected["overdue"] == BIG.overdue
    assert expected["mine"] == 60 + 240 and expected["snoozed"] == 200
    async with tx(app_pool, str(world.client_id)) as conn:
        counts = await fetch_counts(conn, world.client_id, world.me)
        await custom_plans(conn)
        plan = await _explain(
            conn, COUNTS_QUERY, {"client_id": world.client_id, "user_id": world.me}
        )
    async with tx(app_pool, str(world.other_id)) as conn:
        theirs = await fetch_counts(conn, world.other_id, world.me)
    print(f"\n[counts] {_summary(plan)}")
    assert counts.model_dump() == expected
    assert theirs.model_dump() == other
    nodes = list(_nodes(plan))
    used = {n.get("Index Name") for n in nodes}
    assert used & NEEDS_STAFF_INDEXES, _summary(plan)
    assert {"conversations_queue_waiting_idx", "conversations_queue_snoozed_idx"} <= used
    assert "conversations_activity_idx" not in used
    assert not [n for n in nodes if n["Node Type"] == "Seq Scan" and _on_conversations(n)]
    assert not [n for n in nodes if "Sort" in n["Node Type"]], _summary(plan)


async def test_the_other_company_never_shows(
    world: World, brute: Rows, app_pool: AsyncConnectionPool
) -> None:
    seen: set[UUID] = set()
    async with tx(app_pool, str(world.client_id)) as conn:
        for tab in TABS:
            rows, _ = await fetch_page(
                conn,
                _filter(tab),
                client_id=world.client_id,
                user_id=world.me,
                search=None,
                cursor=None,
                limit=50,
            )
            seen |= {r["id"] for r in rows}
        found, _ = await fetch_page(
            conn,
            QueueFilter(q="Person 42"),
            client_id=world.client_id,
            user_id=world.me,
            search=parse_search("Person 42"),
            cursor=None,
            limit=50,
        )
        seen |= {r["id"] for r in found}
    assert seen and not seen & brute.other
    assert seen <= {r["id"] for r in brute.rows}
