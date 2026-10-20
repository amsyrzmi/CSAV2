"""Gateway health endpoint tests (LG01 §8.3).

* ``/healthz`` returns 200 without touching the DB.
* ``/readyz`` returns 200 against a fully-migrated DB and 503 naming the gap
  when a shipped migration version is missing from ``schema_migrations``.
"""  # LG01

from __future__ import annotations

import pytest
from apps.gateway.main import (
    EXPECTED_SCHEMA_VERSION,
    check_readiness,
    shipped_versions,
)
from csa.core import db
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import CSA_APP_PASSWORD, requires_docker


def test_healthz_does_no_io() -> None:
    """/healthz returns 200 {"status": "ok"} with no DB pool involved."""
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:  # mirrors apps.gateway.main
        return {"status": "ok"}

    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_shipped_versions_includes_expected() -> None:
    """The shipped .sql set includes 0150 and 0130 (.py) is NOT counted."""
    versions = shipped_versions()
    assert EXPECTED_SCHEMA_VERSION in versions
    assert "0130" not in versions  # the guarded re-encrypt skeleton is a .py file
    assert max(versions) == EXPECTED_SCHEMA_VERSION


def _app_dsn(container: object) -> str:
    host = container.get_container_host_ip()  # type: ignore[attr-defined]
    port = container.get_exposed_port(5432)  # type: ignore[attr-defined]
    return f"postgresql://csa_app:{CSA_APP_PASSWORD}@{host}:{port}/csa"


@requires_docker
@pytest.mark.asyncio
async def test_readyz_ready_when_fully_migrated(app_dsn: str) -> None:
    """check_readiness is True + 'ok' against a fully-migrated DB."""
    pool = db.build_pool(app_dsn, min_size=1, max_size=2)
    await pool.open(wait=True)
    try:
        ready, reason = await check_readiness(pool)
        assert ready is True
        assert reason == "ok"
    finally:
        await pool.close()


@requires_docker
@pytest.mark.asyncio
@pytest.mark.parametrize("missing_version", ["0140", EXPECTED_SCHEMA_VERSION])
async def test_readyz_503_names_missing_version(
    migrated_dsn: str, pg_container: object, missing_version: str
) -> None:
    """When a shipped version is absent from the ledger, /readyz reports 503.

    Delete a required ledger row (as owner csa) so the readiness probe run as
    csa_app detects the gap and names the missing version.
    """
    import psycopg

    # Remove the ledger row as owner so the gap is visible to the probe.
    with psycopg.connect(migrated_dsn, autocommit=True) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version = %s", (missing_version,))

    pool = db.build_pool(_app_dsn(pg_container), min_size=1, max_size=2)
    await pool.open(wait=True)
    try:
        ready, reason = await check_readiness(pool)
        assert ready is False
        assert missing_version in reason
    finally:
        await pool.close()
        # Restore the ledger row so other module-scoped tests see a full ledger.
        with psycopg.connect(migrated_dsn, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (%s) "
                "ON CONFLICT (version) DO NOTHING",
                (missing_version,),
            )
