"""Register, enable or disable the TikTok Shop app row in ``ecommerce_apps``.

Operator command, run on the owner connection (``csa_app`` can only read the table):
``python -m scripts.tiktok_app register|enable|disable [--app-id ID]``.
Needs ``MIGRATE_DATABASE_URL``, the same owner DSN setting ``scripts.migrate`` uses.
Output is the new state and revision only: never a DSN, key or secret.

Rotating the secret: disable, swap the secret file, recreate the services, enable. Each
transition bumps the revision, which fences every in-flight read.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Literal

from csa.agents.ecommerce.apps import register_app, transition_app
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.core import db
from csa.core.settings import Settings
from psycopg import AsyncConnection

DEFAULT_APP_ID: str = Settings.model_fields["TIKTOK_APP_ID"].default
_TARGET: dict[str, Literal["active", "disabled"]] = {"enable": "active", "disable": "disabled"}


async def run(dsn: str, command: str, app_id: str) -> str:
    """Apply one command and return the line to print. Raises TikTokAuthorizationError."""
    # The connection context commits on a clean exit, before the caller touches any secret.
    async with await AsyncConnection.connect(dsn) as conn:
        if command == "register":
            done = await register_app(conn, app_id)
            return f"{done.app_id}: {done.state} at revision {done.revision}"
        row = await db.q.fetch_one("ecommerce/get_app.sql", (app_id,), conn=conn)
        if row is None:
            raise TikTokAuthorizationError("app_not_registered")
        target = _TARGET[command]
        if row["state"] == target:
            return f"{app_id}: {target} at revision {row['revision']} (unchanged)"
        done = await transition_app(
            conn, app_id=app_id, expected_revision=row["revision"], state=target
        )
        return f"{done.app_id}: {done.state} at revision {done.revision}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.tiktok_app", description=__doc__)
    parser.add_argument("command", choices=["register", "enable", "disable"])
    parser.add_argument("--app-id", default=DEFAULT_APP_ID)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    dsn = os.environ.get("MIGRATE_DATABASE_URL")
    if not dsn:
        print("MIGRATE_DATABASE_URL (the owner DSN) must be set", file=sys.stderr)
        return 2
    try:
        print(asyncio.run(run(dsn, args.command, args.app_id)))
    except TikTokAuthorizationError as error:
        print(f"refused: {error.code}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
