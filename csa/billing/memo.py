"""An in-process memo of closed cycles' usage summaries (``CycleUsage``), memory only.

Computing a cycle costs about 4 s at 20,000 executions, and the Credit tab's execution pages and
cycle picker ask for the same closed cycles again and again. A closed cycle's figures depend only
on its ledger rows, the four config tables and the company's plan rows, so they are memoised under
``(client_id, cycle start, fingerprint)``, where the fingerprint (``billing/memo_fingerprint.sql``,
one small query) changes whenever a config or plan row is added: a back-dated price that prices a
``pending_prices`` cycle, for instance, is a new key, never a stale hit.

Only cycles closed for a full day are cached (``cacheable``): a trace that straddles the boundary
can still add rows to the closed cycle shortly after 00:00, and the open cycle changes with every
call. Those are always computed fresh.

Nothing is stored in the database (the spec's non-goal: no stored statement). The memo is bounded
by entries and by the executions its entries hold (one 20,000-execution cycle is about 17 MB), and
evicts the least recently used. ``USAGE_MEMO`` is the process's one memo; ``clear()`` empties it
for tests. A ``CycleUsage`` handed out is shared: callers must not mutate its lists or dict.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

import psycopg

from csa.billing.cycles import KL, Cycle
from csa.core import db

if TYPE_CHECKING:
    from csa.billing.service import CycleUsage

__all__ = [
    "GRACE",
    "MAX_ENTRIES",
    "MAX_EXECUTIONS",
    "USAGE_MEMO",
    "Fingerprint",
    "MemoKey",
    "UsageMemo",
    "cacheable",
    "fingerprint",
]

GRACE = timedelta(days=1)
MAX_ENTRIES = 64
MAX_EXECUTIONS = 200_000

Fingerprint = tuple[Any, ...]
MemoKey = tuple[UUID, date, Fingerprint]


def cacheable(cycle: Cycle, now: datetime) -> bool:
    """Whether ``cycle`` may be memoised at ``now`` (aware): a full day after it closed."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must carry a timezone")
    return now >= datetime.combine(cycle.end, time(0), tzinfo=KL) + GRACE


async def fingerprint(conn: psycopg.AsyncConnection[Any], client_id: UUID) -> Fingerprint:
    """The config and plan fingerprint, on ``conn`` scoped to ``client_id``.

    Read it before the figures it keys: a row added in between then makes a key no later request
    has (fingerprints only grow), never a stale one.
    """
    row = await db.q.fetch_one("billing/memo_fingerprint.sql", {"client_id": client_id}, conn=conn)
    if row is None:  # a SELECT of scalar subqueries always returns one row
        raise RuntimeError("memo_fingerprint.sql returned no row")
    return tuple(row.values())


class UsageMemo:
    """A least-recently-used map of ``MemoKey`` to ``CycleUsage``, bounded two ways."""

    def __init__(
        self, max_entries: int = MAX_ENTRIES, max_executions: int = MAX_EXECUTIONS
    ) -> None:
        if max_entries < 1 or max_executions < 0:
            raise ValueError("a memo needs room for at least one entry")
        self._max_entries = max_entries
        self._max_executions = max_executions
        self._entries: OrderedDict[MemoKey, CycleUsage] = OrderedDict()
        self._executions = 0
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: MemoKey) -> CycleUsage | None:
        """The memoised usage for ``key`` (now the most recently used), or ``None``."""
        with self._lock:
            usage = self._entries.get(key)
            if usage is not None:
                self._entries.move_to_end(key)
            return usage

    def put(self, key: MemoKey, usage: CycleUsage) -> None:
        """Remember ``usage``; evict the least recently used past either bound.

        A cycle with more executions than the whole budget is not kept at all.
        """
        size = len(usage.executions)
        if size > self._max_executions:
            return
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._executions -= len(previous.executions)
            self._entries[key] = usage
            self._executions += size
            while len(self._entries) > self._max_entries or self._executions > self._max_executions:
                _, evicted = self._entries.popitem(last=False)
                self._executions -= len(evicted.executions)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._executions = 0


USAGE_MEMO = UsageMemo()
