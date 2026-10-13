"""Database access: pool, path-safe query loader, class_row models (LG01 §4).

One :class:`psycopg_pool.AsyncConnectionPool` per process (psycopg3 only;
asyncpg is banned). A small query loader object ``q`` reads cached ``.sql`` from
``db/sql/`` with path-traversal rejection and always-parameterized params, with
an optional Pydantic row model mapped via :func:`psycopg.rows.class_row`.
"""  # LG01

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any, LiteralString, TypeVar, cast

import psycopg
from psycopg.rows import class_row, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

# db/sql lives at <repo-root>/db/sql; this module is <repo-root>/csa/core/db.py.
DB_SQL_DIR = Path(__file__).resolve().parents[2] / "db" / "sql"

_ModelT = TypeVar("_ModelT", bound=BaseModel)

# Default sizing per process class (design §4.1).
GATEWAY_WORKER_MAX_SIZE = 8
DELIVERY_MAX_SIZE = 4


def build_pool(
    conninfo: str, *, min_size: int = 1, max_size: int = 8, autocommit: bool = False
) -> AsyncConnectionPool:
    """Build an unopened async pool with ``dict_row`` as the default row factory.

    The pool is created with ``open=False``; the caller must
    ``await pool.open(wait=True)`` at startup and ``await pool.close()`` on
    shutdown (avoids the psycopg deprecation of opening in ``__init__``).
    Gateway autocommit avoids implicit transactions for unscoped lookups;
    tenant operations still require an explicit :func:`csa.core.tenancy.tx`.
    """
    return AsyncConnectionPool(
        conninfo=conninfo,
        min_size=min_size,
        max_size=max_size,
        kwargs={"row_factory": dict_row, "autocommit": autocommit},
        open=False,
    )


@cache
def _read_sql(path: str) -> LiteralString:
    """Read and cache a ``.sql`` file resolved under :data:`DB_SQL_DIR`.

    Rejects any path containing ``..`` or a leading ``/`` (no traversal). A
    missing file raises :class:`FileNotFoundError` (programmer error). The text
    originates only from trusted repo files, so it is typed ``LiteralString``
    for psycopg's query API (params are still always parameterized separately).
    """
    if path.startswith("/") or ".." in Path(path).parts:
        raise ValueError(f"unsafe query path: {path!r}")
    resolved = (DB_SQL_DIR / path).resolve()
    # Defense in depth: the resolved path must stay under DB_SQL_DIR.
    if not resolved.is_relative_to(DB_SQL_DIR):
        raise ValueError(f"query path escapes db/sql: {path!r}")
    return cast(LiteralString, resolved.read_text(encoding="utf-8"))


class QueryLoader:
    """Loads parameterized ``.sql`` from ``db/sql/`` and runs it on a connection.

    Every query runs on a caller-supplied connection (so tenancy / transaction
    scope is controlled by the caller via :func:`csa.core.tenancy.tx`). Params
    are always passed to psycopg as a mapping/sequence, never interpolated.
    """

    @staticmethod
    def load(path: str) -> LiteralString:
        """Return the cached SQL text for ``path`` (relative to ``db/sql/``)."""
        return _read_sql(path)

    async def fetch_one(
        self,
        path: str,
        params: Any = None,
        *,
        conn: psycopg.AsyncConnection[Any],
        model: type[_ModelT] | None = None,
    ) -> Any:
        """Run the query and return the first row (or ``None``).

        With ``model=`` the row is a Pydantic instance via ``class_row``;
        otherwise it is a ``dict`` (the pool default).
        """
        sql = self.load(path)
        row_factory = class_row(model) if model is not None else dict_row
        async with conn.cursor(row_factory=row_factory) as cur:
            await cur.execute(sql, params)
            return await cur.fetchone()

    async def fetch_all(
        self,
        path: str,
        params: Any = None,
        *,
        conn: psycopg.AsyncConnection[Any],
        model: type[_ModelT] | None = None,
    ) -> list[Any]:
        """Run the query and return all rows (dicts, or ``model`` instances)."""
        sql = self.load(path)
        row_factory = class_row(model) if model is not None else dict_row
        async with conn.cursor(row_factory=row_factory) as cur:
            await cur.execute(sql, params)
            return await cur.fetchall()

    async def execute(
        self,
        path: str,
        params: Any = None,
        *,
        conn: psycopg.AsyncConnection[Any],
    ) -> None:
        """Run a non-returning statement (INSERT/UPDATE/DELETE/DDL-free)."""
        sql = self.load(path)
        async with conn.cursor() as cur:
            await cur.execute(sql, params)


q = QueryLoader()
"""Process-wide query loader instance."""
