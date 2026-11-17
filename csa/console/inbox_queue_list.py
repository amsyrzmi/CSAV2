"""The Inbox work queue list: its filters, orders, keyset paging and the tab counts.

The filters combine with AND (design 6.2). `QueueFilter` is the shape of the list's query
parameters (the saved views store the same shape). The four orders each have their own SQL file
(``LIST_QUERIES``) sharing one filter block; the page is read from ``conversations`` alone and the
contact, last message and counts are added for its rows only. Search text is personal data: the
callers parse it with ``parse_search`` and never log it.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from psycopg import AsyncConnection
from pydantic import Field

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


class QueueFilter(Contract):
    """The list's filters and order. Empty lists, None and False filter nothing.

    `assignee` is ``me`` (the person asking), ``none`` (no assignee) or a user id (any id is
    accepted: one that holds nothing matches nothing). `overdue` and `waiting_over` imply
    ``needs_staff``. Without `order` the list is due-first when `state` is exactly
    ``needs_staff``, newest activity first otherwise.
    """

    state: list[QueueState] = []
    assignee: Literal["me", "none"] | UUID | None = None
    priority_min: QueuePriority | None = None
    reason: list[QueueReason] = []
    topic: list[QueueTopic] = []
    overdue: bool = False
    waiting_over: Annotated[int, Field(ge=1, le=WAITING_OVER_MAX)] | None = None
    channel: Literal["telegram", "whatsapp"] | None = None
    needs_review: bool = False
    sender: Literal["known", "new"] | None = None
    q: Annotated[str, Field(max_length=64)] | None = None
    order: QueueOrder | None = None

    def effective_order(self) -> QueueOrder:
        if self.order is not None:
            return self.order
        return "due" if set(self.state) == {"needs_staff"} else "activity"

    def needs_staff_only(self) -> bool:
        """Every row in scope is needs_staff, so each has a due time and a waiting-since time."""
        only_state = bool(self.state) and set(self.state) == {"needs_staff"}
        return only_state or self.overdue or self.waiting_over is not None


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
    if cursor is not None and cursor.legacy:
        found = await db.q.fetch_one(
            "inbox/cursor_activity.sql",
            {"client_id": client_id, "conversation_id": cursor.id},
            conn=conn,
        )
        if found is None:
            return [], None
        cursor = replace(cursor, at=found["last_activity_at"], legacy=False)
    params = list_params(
        flt, client_id=client_id, user_id=user_id, search=search, cursor=cursor, limit=limit + 1
    )
    rows = await db.q.fetch_all(LIST_QUERIES[order], params, conn=conn)
    page = rows[:limit]
    return page, _cursor_of(order, page[-1]) if len(rows) > limit else None


async def fetch_counts(
    conn: AsyncConnection[Any], client_id: UUID, user_id: UUID
) -> InboxQueueCounts:
    """The tab counts, read from the queue's partial indexes."""
    row = await db.q.fetch_one(
        "inbox/queue_counts.sql", {"client_id": client_id, "user_id": user_id}, conn=conn
    )
    if row is None:
        raise RuntimeError("queue_counts_returned_no_row")
    return InboxQueueCounts.model_validate(row)
