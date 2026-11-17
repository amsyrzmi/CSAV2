"""The Inbox queue list's opaque keyset cursor.

A token is base64url (no padding) of ``<order>|<sort time>|<id>``; the priority order adds its
rank: ``priority|<rank>|<due time>|<id>``. The sort time is ISO 8601 with its offset, empty when
the row has none (``due_at`` and ``needs_staff_since`` are NULL outside ``needs_staff``; those rows
sort last). The order is part of the token so a cursor from one order is refused by another
instead of paging it wrongly. A bare conversation id, the cursor older clients send, is accepted
as an activity cursor; its time is looked up by the list. Anything else is a ``ValueError`` (the
route answers 422).
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

QueueOrder = Literal["activity", "waiting", "due", "priority"]

_ORDERS: frozenset[str] = frozenset({"activity", "waiting", "due", "priority"})
_MAX_TOKEN = 200
_TOKEN = re.compile(r"[A-Za-z0-9_-]+")
_BARE_ID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_RANK = re.compile(r"[1-4]")


@dataclass(frozen=True)
class QueueCursor:
    """Where the next page starts: after the row with this sort value and id.

    ``legacy`` marks a bare conversation id: ``at`` is unknown until the list reads it.
    """

    order: QueueOrder
    at: datetime | None
    id: UUID
    rank: int | None = None
    legacy: bool = False


def encode(order: QueueOrder, *, at: datetime | None, id: UUID, rank: int | None = None) -> str:
    """The token for the row a page ended on."""
    if order not in _ORDERS:
        raise ValueError("unknown order")
    if order == "activity" and at is None:
        raise ValueError("the activity cursor needs a time")
    if at is not None and at.utcoffset() is None:
        raise ValueError("the cursor time needs an offset")
    if (order == "priority") != (rank is not None):
        raise ValueError("only the priority cursor carries a rank")
    if rank is not None and not 1 <= rank <= 4:
        raise ValueError("the rank is 1 to 4")
    ranked = [str(rank)] if rank is not None else []
    parts = [order, *ranked, at.isoformat() if at else "", str(id)]
    return base64.urlsafe_b64encode("|".join(parts).encode()).decode().rstrip("=")


def _time(text: str) -> datetime | None:
    if not text:
        return None
    at = datetime.fromisoformat(text)
    if at.utcoffset() is None:
        raise ValueError("the cursor time needs an offset")
    return at


def decode(token: str, order: QueueOrder) -> QueueCursor:
    """The cursor in ``token`` for a list in ``order``; ``ValueError`` when it is not one."""
    if _BARE_ID.fullmatch(token):
        if order != "activity":
            raise ValueError("a conversation id cursor pages only the activity order")
        return QueueCursor(order="activity", at=None, id=UUID(token), legacy=True)
    if len(token) > _MAX_TOKEN or not _TOKEN.fullmatch(token):
        raise ValueError("malformed cursor")
    try:
        text = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4)).decode()
    except (binascii.Error, UnicodeDecodeError):
        raise ValueError("malformed cursor") from None
    parts = text.split("|")
    if not parts or parts[0] != order:
        raise ValueError("the cursor belongs to another order")
    rank: int | None = None
    if order == "priority":
        if len(parts) != 4 or not _RANK.fullmatch(parts[1]):
            raise ValueError("malformed cursor")
        rank = int(parts[1])
        parts = [parts[0], *parts[2:]]
    if len(parts) != 3:
        raise ValueError("malformed cursor")
    at = _time(parts[1])
    if order == "activity" and at is None:
        raise ValueError("the activity cursor needs a time")
    cursor = QueueCursor(order=order, at=at, id=UUID(parts[2]), rank=rank)
    if encode(order, at=at, id=cursor.id, rank=rank) != token:
        raise ValueError("malformed cursor")
    return cursor
