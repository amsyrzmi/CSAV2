"""DB pool + path-safe query loader + class_row tests (LG01 §4.1-4.3)."""  # LG01

from __future__ import annotations

import pytest
from csa.core import db
from pydantic import BaseModel

from tests.conftest import requires_docker


def test_loader_rejects_leading_slash() -> None:
    with pytest.raises(ValueError):
        db.QueryLoader.load("/etc/passwd")


def test_loader_rejects_parent_traversal() -> None:
    with pytest.raises(ValueError):
        db.QueryLoader.load("../settings.py")


def test_loader_reads_and_caches_ping() -> None:
    first = db.QueryLoader.load("_ping.sql")
    second = db.QueryLoader.load("_ping.sql")
    assert "SELECT 1" in first
    assert first is second  # lru_cache returns the identical string object


class _Ping(BaseModel):
    value: int


@requires_docker
@pytest.mark.asyncio
async def test_pool_parameterized_and_class_row(app_dsn: str) -> None:
    pool = db.build_pool(app_dsn, min_size=1, max_size=2)
    await pool.open(wait=True)
    try:
        async with pool.connection() as conn:
            # Parameterized query via the loader (dict_row default).
            async with conn.cursor() as cur:
                await cur.execute("SELECT %s::int AS value", (7,))
                row = await cur.fetchone()
            assert row == {"value": 7}

            # class_row maps a row into the Pydantic model.
            ping = await db.q.fetch_one("_ping.sql", conn=conn, model=_Ping)
            assert isinstance(ping, _Ping)
            assert ping.value == 1
    finally:
        await pool.close()
