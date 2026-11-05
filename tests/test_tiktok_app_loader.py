"""App loader: key and secret from settings, revision and state from the registry row."""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest
import pytest_asyncio
from csa.agents.ecommerce.app_loader import build_app_loader
from csa.agents.ecommerce.apps import register_app, transition_app
from csa.core import db
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_console_identity_config import configured

pytestmark = [requires_docker, pytest.mark.asyncio]

REAL = {
    "TIKTOK_APP_KEY": "synthetic-app-key",
    "TIKTOK_APP_SECRET": "synthetic-app-secret",
    "TIKTOK_SERVICE_ID": "synthetic-service-id",
}


@pytest_asyncio.fixture
async def pool(app_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(app_dsn, max_size=2)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


async def activate(owner_dsn: str, app_id: str, revisions: int = 1) -> None:
    """Register ``app_id`` and flip it ``revisions`` times, ending active (odd) or disabled."""
    async with await psycopg.AsyncConnection.connect(owner_dsn) as conn:
        state = await register_app(conn, app_id)
        for _ in range(revisions):
            state = await transition_app(
                conn,
                app_id=app_id,
                expected_revision=state.revision,
                state="active" if state.state == "disabled" else "disabled",
            )


async def test_loader_returns_material_with_row_revision(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    # register (1, disabled) -> active (2) -> disabled (3) -> active (4)
    await activate(migrated_dsn, "loader_active", revisions=3)
    settings = configured(**REAL, TIKTOK_APP_ID="loader_active")
    app = await build_app_loader(settings, pool)("loader_active")
    assert app is not None
    assert app.app_id == "loader_active"
    assert app.revision == 4
    assert app.enabled is True
    assert app.app_key.get_secret_value() == "synthetic-app-key"
    assert app.app_secret.get_secret_value() == "synthetic-app-secret"


async def test_loader_revision_never_comes_from_settings(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    await activate(migrated_dsn, "loader_rev", revisions=1)
    settings = configured(**REAL, TIKTOK_APP_ID="loader_rev")
    load = build_app_loader(settings, pool)
    first = await load("loader_rev")
    assert first is not None and first.revision == 2
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await transition_app(conn, app_id="loader_rev", expected_revision=2, state="disabled")
        await transition_app(conn, app_id="loader_rev", expected_revision=3, state="active")
    second = await load("loader_rev")
    assert second is not None and second.revision == 4


async def test_loader_none_when_off_disabled_or_wrong_id(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    await activate(migrated_dsn, "loader_cases", revisions=1)
    on = configured(**REAL, TIKTOK_APP_ID="loader_cases")
    assert await build_app_loader(on, pool)("loader_cases") is not None
    # Off: one of the three secrets is the staging sentinel.
    off = configured(**{**REAL, "TIKTOK_SERVICE_ID": "disabled"}, TIKTOK_APP_ID="loader_cases")
    assert await build_app_loader(off, pool)("loader_cases") is None
    # Wrong id: only TIKTOK_APP_ID is served, even if the other row exists and is active.
    await activate(migrated_dsn, "loader_other", revisions=1)
    assert await build_app_loader(on, pool)("loader_other") is None
    # Missing row.
    missing = configured(**REAL, TIKTOK_APP_ID="loader_missing")
    assert await build_app_loader(missing, pool)("loader_missing") is None
    # Disabled row.
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await transition_app(conn, app_id="loader_cases", expected_revision=2, state="disabled")
    assert await build_app_loader(on, pool)("loader_cases") is None
    # Registered but never enabled.
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await register_app(conn, "loader_fresh")
    fresh = configured(**REAL, TIKTOK_APP_ID="loader_fresh")
    assert await build_app_loader(fresh, pool)("loader_fresh") is None
