"""Explicit staging migration gate; never called by application startup."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlsplit

from scripts import lg_setup, migrate

SECRET_NAMES = (
    "MIGRATE_DATABASE_URL",
    "LG_SETUP_DATABASE_URL",
    "CSA_APP_PASSWORD",
    "CSA_RO_PASSWORD",
    "CSA_IDENTITY_PASSWORD",
)


def approved_target(approval: str | None, dsn: str) -> bool:
    """Restrict this proposal to its isolated database and owner role."""
    url = urlsplit(dsn)
    return (
        approval == "approved-isolated-staging"
        and url.scheme in {"postgres", "postgresql"}
        and url.hostname == "staging-db"
        and url.port in {None, 5432}
        and url.username == "csa"
        and url.path == "/csa_staging"
        and not url.query
        and not url.fragment
    )


def main() -> None:
    if os.environ.get("CSA_MIGRATION_APPROVAL") != "approved-isolated-staging":
        raise RuntimeError("Explicit isolated-staging migration approval required")
    for name in SECRET_NAMES:
        os.environ[name] = (Path("/run/secrets") / name).read_text().strip()
    if not all(
        approved_target(os.environ["CSA_MIGRATION_APPROVAL"], os.environ[name])
        for name in ("MIGRATE_DATABASE_URL", "LG_SETUP_DATABASE_URL")
    ):
        raise RuntimeError("Migration target must be the isolated staging database")
    if migrate.main() != 0:
        raise RuntimeError("Staging migration failed; checkpoint setup not run")
    asyncio.run(lg_setup.setup(os.environ["LG_SETUP_DATABASE_URL"]))


if __name__ == "__main__":
    main()
