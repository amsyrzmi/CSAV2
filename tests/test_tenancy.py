"""Transaction-local tenancy GUC tests (LG01 §4.4)."""  # LG01

from __future__ import annotations

from typing import Any

import pytest
from csa.core import db
from csa.core.tenancy import tx

from tests.conftest import requires_docker

_CLIENT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _row(value: Any) -> dict[str, Any]:
    """Assert a fetched row is present and return it (narrows Optional)."""
    assert value is not None
    return value


@pytest.mark.asyncio
async def test_malformed_client_id_raises(app_dsn: str) -> None:
    # No DB round-trip needed: validation happens before borrowing a connection.
    pool = db.build_pool(app_dsn, min_size=1, max_size=1)
    with pytest.raises(ValueError):
        async with tx(pool, "not-a-uuid"):
            pass
    await pool.close()


@requires_docker
@pytest.mark.asyncio
async def test_guc_set_inside_tx(app_dsn: str) -> None:
    pool = db.build_pool(app_dsn, min_size=1, max_size=2)
    await pool.open(wait=True)
    try:
        async with tx(pool, _CLIENT_A) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT current_setting('app.client_id', true) AS cid")
                row = _row(await cur.fetchone())
            assert row["cid"] == _CLIENT_A
    finally:
        await pool.close()


@requires_docker
@pytest.mark.asyncio
@pytest.mark.parametrize("autocommit", [False, True])
async def test_guc_is_transaction_local_across_borrows(app_dsn: str, autocommit: bool) -> None:
    # A single-connection pool guarantees the same physical connection is reused,
    # proving the GUC did not leak from the first tx to the second borrow.
    pool = db.build_pool(app_dsn, min_size=1, max_size=1, autocommit=autocommit)
    await pool.open(wait=True)
    try:
        async with tx(pool, _CLIENT_A) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT current_setting('app.client_id', true) AS cid")
                assert _row(await cur.fetchone())["cid"] == _CLIENT_A

        # Borrow the same connection again, outside any tx: the GUC is reset.
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT current_setting('app.client_id', true) AS cid")
                assert _row(await cur.fetchone())["cid"] == ""
    finally:
        await pool.close()
