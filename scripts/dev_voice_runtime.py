"""Run one presentation service only against the isolated dev database."""

from __future__ import annotations

import argparse
import asyncio
from typing import Literal

from csa.core.logging import configure_logging, shutdown_logging
from csa.core.settings import Settings, get_settings
from psycopg.conninfo import conninfo_to_dict
from pydantic import PostgresDsn

Service = Literal["gateway", "worker", "delivery", "poller"]


def validate(settings: Settings, service: Service) -> None:
    if settings.ENV != "dev":
        raise ValueError("presentation services require ENV=dev")
    targets: list[tuple[PostgresDsn | None, str]] = [
        (settings.DATABASE_URL, "csa_app"),
        (settings.LG_DATABASE_URL, "csa"),
    ]
    if service == "gateway":
        targets.append((settings.IDENTITY_DATABASE_URL, "csa_identity"))
    expected = {"host": "127.0.0.1", "port": "5434", "dbname": "csa"}
    for dsn, role in targets:
        if dsn is None:
            raise ValueError("presentation database is not configured")
        params = conninfo_to_dict(str(dsn))
        if (
            not all(params.get(k) == v for k, v in expected.items())
            or params.get("user") != role
            or any(params.get(k) for k in ("hostaddr", "service"))
        ):
            raise ValueError("refusing presentation startup: isolated target/role mismatch")
    if service == "worker" and settings.TURN_ENGINE != "coordinator":
        raise ValueError("presentation voice requires TURN_ENGINE=coordinator")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=["gateway", "worker", "delivery", "poller"])
    args = parser.parse_args()
    settings = get_settings()
    validate(settings, args.service)
    if args.service == "gateway":
        import uvicorn

        uvicorn.run("apps.gateway.main:app", host="127.0.0.1", port=18081)
        return
    configure_logging(env="dev")
    try:
        if args.service == "worker":
            from apps.worker.main import main as run
        elif args.service == "delivery":
            from apps.delivery.main import main as run
        else:
            from apps.telegram_poller.main import run_poller as run
        asyncio.run(run())
    finally:
        shutdown_logging()


if __name__ == "__main__":
    main()
