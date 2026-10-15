"""Shared pytest fixtures for the DB integration tests (LG01 FEAT-002).

Builds the combined PostGIS + pgvector image from ``deploy/db.Dockerfile`` once
per session, then starts a throwaway Postgres container per test module. The
baseline schema plus the LG01 migrations are applied via ``scripts.migrate`` so
tests exercise the real migration path.
"""  # LG01

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_DOCKERFILE = REPO_ROOT / "deploy" / "db.Dockerfile"
SCHEMA_BASELINE = REPO_ROOT / "db" / "schema_baseline.sql"

_IMAGE_TAG = "csa/pg16-postgis-vector:test"

# Fixed, NON-SECRET local test passwords (same shape as .env.example dev values).
CSA_APP_PASSWORD = "test_app_pw"
CSA_RO_PASSWORD = "test_ro_pw"


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:  # pragma: no cover - environment probe
        return False


requires_docker = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker is not available on this host",
)


@pytest.fixture(scope="session")
def combined_pg_image() -> str:
    """Build (once) the combined PostGIS+pgvector image and return its tag."""
    import docker

    client = docker.from_env()
    client.images.build(
        # The image needs no application files. Never send environment files,
        # copied checkouts or archives in Docker's build context.
        path=str(DB_DOCKERFILE.parent),
        dockerfile=DB_DOCKERFILE.name,
        tag=_IMAGE_TAG,
        rm=True,
    )
    return _IMAGE_TAG


@pytest.fixture(scope="module")
def pg_container(combined_pg_image: str) -> Iterator[object]:
    """Start a Postgres container on the combined image (superuser ``csa``)."""
    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer(
        image=combined_pg_image,
        username="csa",
        password="csa_owner_pw",
        dbname="csa",
        driver=None,
    )
    with container as running:
        yield running


def _owner_dsn(container: object) -> str:
    """Build a libpq DSN connecting as the superuser/owner ``csa``."""
    host = container.get_container_host_ip()  # type: ignore[attr-defined]
    port = container.get_exposed_port(5432)  # type: ignore[attr-defined]
    return f"postgresql://csa:csa_owner_pw@{host}:{port}/csa"


def _app_dsn(container: object, *, role: str = "csa_app", password: str = CSA_APP_PASSWORD) -> str:
    host = container.get_container_host_ip()  # type: ignore[attr-defined]
    port = container.get_exposed_port(5432)  # type: ignore[attr-defined]
    return f"postgresql://{role}:{password}@{host}:{port}/csa"


def _apply_baseline(container: object) -> None:
    """Apply the schema baseline via psql inside the container.

    The baseline is a pg_dump that uses psql meta-commands (``\\restrict`` /
    ``\\unrestrict``), so it must be applied by psql, not by psycopg's protocol
    ``execute``. This mirrors the design's apply path (make db-reset / the
    compose migrate one-shot pipe the dump through psql).
    """
    data = SCHEMA_BASELINE.read_bytes()
    container.copy_into_container(data, "/tmp/schema_baseline.sql")  # type: ignore[attr-defined]
    code, output = container.exec(  # type: ignore[attr-defined]
        [
            "psql",
            "-U",
            "csa",
            "-d",
            "csa",
            "-v",
            "ON_ERROR_STOP=1",
            "-f",
            "/tmp/schema_baseline.sql",
        ]
    )
    if code != 0:
        raise RuntimeError(
            f"baseline apply failed (exit {code}):\n{output.decode(errors='replace')}"
        )


def _run_migrations(dsn: str) -> None:
    """Apply db/migrations via the real runner, with env-provided passwords."""
    from scripts import migrate

    old = {
        "MIGRATE_DATABASE_URL": os.environ.get("MIGRATE_DATABASE_URL"),
        "CSA_APP_PASSWORD": os.environ.get("CSA_APP_PASSWORD"),
        "CSA_RO_PASSWORD": os.environ.get("CSA_RO_PASSWORD"),
    }
    os.environ["MIGRATE_DATABASE_URL"] = dsn
    os.environ["CSA_APP_PASSWORD"] = CSA_APP_PASSWORD
    os.environ["CSA_RO_PASSWORD"] = CSA_RO_PASSWORD
    try:
        rc = migrate.main()
        assert rc == 0, "migration runner returned non-zero"
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture(scope="module")
def migrated_dsn(pg_container: object) -> str:
    """Apply baseline + migrations and return the owner (``csa``) DSN."""
    owner = _owner_dsn(pg_container)
    _apply_baseline(pg_container)
    _run_migrations(owner)
    return owner


@pytest.fixture(scope="module")
def app_dsn(migrated_dsn: str, pg_container: object) -> str:
    """Return a ``csa_app`` DSN against the migrated DB."""
    return _app_dsn(pg_container)


def owner_dsn_for(container: object) -> str:
    """Public helper: owner (``csa``) DSN for a running container."""
    return _owner_dsn(container)


@pytest.fixture(scope="module")
def lg_setup_dsn(migrated_dsn: str) -> str:
    """Create the ``lg.*`` checkpoint tables once (as owner csa).

    Runs the real :mod:`scripts.lg_setup` against the migrated DB, so FEAT-003
    tests exercise the actual setup path. Returns the owner DSN.
    """
    import asyncio

    from scripts import lg_setup

    asyncio.run(lg_setup.setup(migrated_dsn))
    return migrated_dsn
