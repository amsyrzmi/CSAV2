"""App-side LangGraph checkpoint pool + saver (LG01 §7.2).

A DEDICATED checkpoint pool, separate from the RLS ``csa_app`` pool in
:mod:`csa.core.db`. The checkpointer requires its connections to be
``autocommit=True`` and ``row_factory=dict_row``; keeping them in their own pool
avoids clashing those requirements with ``tenancy.tx``'s explicit transactions
on the RLS pool.

The pool connects as ``csa_app`` using ``LG_DATABASE_URL`` with
``options='-c search_path=lg'`` so every checkpoint read/write targets the
``lg`` schema. ``lg.*`` tables have no ``client_id`` column, so RLS is never
enabled there and the DML is unfiltered — this rests on the grant from
``0140_lg_schema.sql`` (USAGE on schema lg + default privileges set before
``scripts/lg_setup.py``).

``thread_id`` is the ``trace_id`` (design principle §2). ``setup()`` is NEVER
called here — the tables are created once by :mod:`scripts.lg_setup`.
"""  # LG01

from __future__ import annotations

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

# The checkpoint pool yields dict-row connections (the saver's requirement).
CheckpointPool = AsyncConnectionPool[AsyncConnection[DictRow]]

# Force every checkpoint connection into the lg schema at connect time.
_LG_SEARCH_PATH_OPTION = "-c search_path=lg"


def build_checkpoint_pool(
    lg_database_url: str,
    *,
    min_size: int = 1,
    max_size: int = 4,
) -> CheckpointPool:
    """Build the unopened dedicated checkpoint pool.

    The caller must ``await pool.open(wait=True)`` at startup and
    ``await pool.close()`` on shutdown. Connections are ``autocommit=True`` with
    ``dict_row`` and ``search_path=lg`` — the documented AsyncPostgresSaver
    requirements.
    """
    return AsyncConnectionPool(
        conninfo=lg_database_url,
        min_size=min_size,
        max_size=max_size,
        connection_class=AsyncConnection[DictRow],
        kwargs={
            "autocommit": True,
            "row_factory": dict_row,
            "options": _LG_SEARCH_PATH_OPTION,
        },
        open=False,
    )


def build_saver(pool: CheckpointPool) -> AsyncPostgresSaver:
    """Build an :class:`AsyncPostgresSaver` over an already-opened checkpoint pool.

    ``setup()`` is intentionally not called — the ``lg.*`` tables are created
    once by :mod:`scripts.lg_setup` as the owner ``csa``.
    """
    return AsyncPostgresSaver(pool)
