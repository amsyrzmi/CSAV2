"""The Inbox work queue list: its filters, orders, keyset paging and the tab counts.

The filters combine with AND (design 6.2). `QueueFilter` is the shape of the list's query
parameters; its base `InboxFilters` is the part a saved view stores, validated by the same rules.
The four orders each have their own SQL file (``LIST_QUERIES``) sharing one filter block; the page
is read from ``conversations`` alone and the contact, last message and counts are added for its
rows only. Search text is personal data: the callers parse it with ``parse_search`` and never log
it. Both reads force custom plans (``custom_plans``).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any, Literal, Self, get_args
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import AfterValidator, Field, model_validator

from csa.console.contracts import Contract
from csa.console.inbox_contact import Search
from csa.console.inbox_cursor import QueueCursor, QueueOrder, encode
from csa.core import db

QueueState = Literal["bot", "needs_staff", "waiting_customer", "snoozed", "resolved"]
QueueReason = Literal["escalation", "held_reply", "customer_message", "manual"]
QueueTopic = Literal[
    "purchase", "question", "complaint", "payment", "booking", "subscription", "other"
]
QueuePriority = Literal["low", "normal", "high", "urgent"]

PRIORITY_RANK: dict[str, int] = {"low": 1, "normal": 2, "high": 3, "urgent": 4}
WAITING_OVER_MAX = 525600  # minutes: one year

LIST_QUERIES: dict[QueueOrder, str] = {
    "activity": "inbox/list_queue_activity.sql",
    "waiting": "inbox/list_queue_waiting.sql",
    "due": "inbox/list_queue_due.sql",
    "priority": "inbox/list_queue_priority.sql",
}
COUNTS_QUERY = "inbox/queue_counts.sql"


PRIORITY_ORDER_RULE = "order=priority is only for the state filter needs_staff alone"


def _once_each[T](values: list[T]) -> list[T]:
    """The values in their first order, each once (a filter list is a set)."""
    return list(dict.fromkeys(values))


# A filter list holds at most as many values as its enum has; repeats are dropped.
StateList = Annotated[
    list[QueueState], Field(max_length=len(get_args(QueueState))), AfterValidator(_once_each)
]
ReasonList = Annotated[
    list[QueueReason], Field(max_length=len(get_args(QueueReason))), AfterValidator(_once_each)
]
TopicList = Annotated[
    list[QueueTopic], Field(max_length=len(get_args(QueueTopic))), AfterValidator(_once_each)
]


class InboxFilters(Contract):
    """The list's filters and order, as a saved view stores them (never the search, a cursor
    or a limit). Empty lists, None and False filter nothing.

    `state`, `reason` and `topic` hold each value once (repeats are dropped) and at most as many
    values as there are (5, 4 and 7). `assignee` is ``me`` (the person asking), ``none`` (no
    assignee) or a user id (any id is accepted: one that holds nothing matches nothing).
    `overdue` and `waiting_over` imply ``needs_staff``. Without `order` the list is due-first
    when every row in scope needs staff (`needs_staff_only`), newest activity first otherwise.
    `order=priority` sorts the rows in scope (no index holds the rank), so it is allowed only
    where they are the needs_staff rows.
    """

    state: StateList = []
    assignee: Literal["me", "none"] | UUID | None = None
    priority_min: QueuePriority | None = None
    reason: ReasonList = []
    topic: TopicList = []
    overdue: bool = False
    waiting_over: Annotated[int, Field(ge=1, le=WAITING_OVER_MAX)] | None = None
    channel: Literal["telegram", "whatsapp"] | None = None
    sender: Literal["known", "new"] | None = None
    order: QueueOrder | None = None

    @model_validator(mode="after")
    def _priority_order_is_for_needs_us(self) -> Self:
        if self.order == "priority" and set(self.state) != {"needs_staff"}:
            raise ValueError(PRIORITY_ORDER_RULE)
        return self

    def effective_order(self) -> QueueOrder:
        if self.order is not None:
            return self.order
        return "due" if self.needs_staff_only() else "activity"

    def needs_staff_only(self) -> bool:
        """Every row in scope is needs_staff, so each has a due time and a waiting-since time."""
        only_state = set(self.state) == {"needs_staff"}
        return only_state or self.overdue or self.waiting_over is not None


class QueueFilter(InboxFilters):
    """The list's query parameters: a saved view's filters plus the needs-review flag (kept for
    current clients) and the search text `q` (personal data: never stored, logged or audited)."""

    needs_review: bool = False
    q: Annotated[str, Field(max_length=64)] | None = None


class InboxQueueCounts(Contract):
    """The queue tabs' counts for the company; `mine` is the person asking."""

    needs_us: int
    mine: int
    unassigned: int
    overdue: int
    waiting_customer: int
    snoozed: int
    oldest_waiting_at: datetime | None


def _sorted(values: list[str]) -> list[str] | None:
    return sorted(set(values)) or None


def list_params(
    flt: QueueFilter,
    *,
    client_id: UUID,
    user_id: UUID,
    search: Search | None,
    cursor: QueueCursor | None,
    limit: int,
) -> dict[str, Any]:
    """The named parameters of the list SQL. `cursor` must not be a legacy one (see fetch_page)."""
    if cursor is not None and cursor.legacy:
        raise ValueError("resolve a legacy cursor first")
    if flt.assignee == "me":
        assignee: UUID | None = user_id
    else:
        assignee = flt.assignee if isinstance(flt.assignee, UUID) else None
    priorities = (
        [p for p, rank in PRIORITY_RANK.items() if rank >= PRIORITY_RANK[flt.priority_min]]
        if flt.priority_min
        else None
    )
    return {
        "client_id": client_id,
        "channel": flt.channel,
        "states": _sorted(list(flt.state)),
        "assignee": assignee,
        "unassigned": flt.assignee == "none",
        "priorities": priorities,
        "reasons": _sorted(list(flt.reason)),
        "topics": _sorted(list(flt.topic)),
        "overdue": flt.overdue,
        "waiting_over": flt.waiting_over,
        "needs_review": flt.needs_review,
        "sender": flt.sender,
        "like": search.like if search else None,
        "digits": search.digits if search else None,
        "nulls": not flt.needs_staff_only(),
        "after_at": cursor.at if cursor else None,
        "after_id": cursor.id if cursor else None,
        "after_rank": cursor.rank if cursor else None,
        "limit": limit,
    }


def _cursor_of(order: QueueOrder, row: dict[str, Any]) -> str:
    """The token after the row a page ended on."""
    if order == "activity":
        return encode(order, at=row["last_activity_at"], id=row["id"])
    if order == "waiting":
        return encode(order, at=row["needs_staff_since"], id=row["id"])
    if order == "due":
        return encode(order, at=row["due_at"], id=row["id"])
    return encode(order, at=row["due_at"], id=row["id"], rank=PRIORITY_RANK[row["priority"]])


async def custom_plans(conn: AsyncConnection[Any]) -> None:
    """Plan each list and counts run for its own parameter values, for this transaction only.

    psycopg prepares a query after five runs on one connection, and PostgreSQL may then switch the
    prepared statement to a generic plan, in which ``%(x)s IS NULL OR ...`` is not folded away and
    the partial indexes stop serving the tabs.
    """
    await conn.execute("SET LOCAL plan_cache_mode = force_custom_plan")


def page_query(
    flt: QueueFilter,
    *,
    client_id: UUID,
    user_id: UUID,
    search: Search | None,
    cursor: QueueCursor | None,
    limit: int,
) -> tuple[str, dict[str, Any]]:
    """The SQL file and parameters of one page: `limit` + 1 rows, the extra one telling whether
    a next page exists. `cursor` must not be a legacy one (see fetch_page)."""
    params = list_params(
        flt, client_id=client_id, user_id=user_id, search=search, cursor=cursor, limit=limit + 1
    )
    return LIST_QUERIES[flt.effective_order()], params


async def fetch_page(
    conn: AsyncConnection[Any],
    flt: QueueFilter,
    *,
    client_id: UUID,
    user_id: UUID,
    search: Search | None,
    cursor: QueueCursor | None,
    limit: int,
) -> tuple[list[dict[str, Any]], str | None]:
    """One page of list rows and the cursor of the next page (None on the last one).

    A legacy cursor (a bare conversation id) takes that conversation's activity time; an id
    that is not one of the company's conversations gives an empty page, as it did before.
    """
    order = flt.effective_order()
    await custom_plans(conn)
    if cursor is not None and cursor.legacy:
        found = await db.q.fetch_one(
            "inbox/cursor_activity.sql",
            {"client_id": client_id, "conversation_id": cursor.id},
            conn=conn,
        )
        if found is None:
            return [], None
        cursor = replace(cursor, at=found["last_activity_at"], legacy=False)
    path, params = page_query(
        flt, client_id=client_id, user_id=user_id, search=search, cursor=cursor, limit=limit
    )
    rows = await db.q.fetch_all(path, params, conn=conn)
    page = rows[:limit]
    return page, _cursor_of(order, page[-1]) if len(rows) > limit else None


async def fetch_counts(
    conn: AsyncConnection[Any], client_id: UUID, user_id: UUID
) -> InboxQueueCounts:
    """The tab counts, read from the queue's partial indexes."""
    await custom_plans(conn)
    row = await db.q.fetch_one(
        COUNTS_QUERY, {"client_id": client_id, "user_id": user_id}, conn=conn
    )
    if row is None:
        raise RuntimeError("queue_counts_returned_no_row")
    return InboxQueueCounts.model_validate(row)
