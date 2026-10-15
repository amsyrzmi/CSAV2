"""Deliberately create the first CSAV2 platform administrator.

No dotenv files are read. IDENTITY_DATABASE_URL must be explicitly supplied in
this process environment, using the isolated csa_identity login role. Passwords
are read with getpass, never argv, output or source. Existing accounts are never
imported. Bootstrap closes permanently after the first platform admin exists.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from urllib.parse import urlparse

from csa.console.identity import IdentityConfig, IdentityService
from csa.core.db import build_pool


async def run(email: str, display_name: str, password: str) -> None:
    dsn = os.environ.get("IDENTITY_DATABASE_URL", "")
    if not dsn or urlparse(dsn).username != "csa_identity":
        raise ValueError("set IDENTITY_DATABASE_URL explicitly using the csa_identity login role")
    pool = build_pool(dsn, min_size=1, max_size=1)
    await pool.open(wait=True)
    try:
        service = IdentityService(pool, IdentityConfig(origins=("http://localhost",)))
        await service.check_ready()
        user_id = await service.bootstrap(email, password, display_name)
        print(f"Created platform administrator {user_id}; password change required at first login.")
    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--create-first-platform-admin",
        action="store_true",
        required=True,
        help="explicitly acknowledge creating the first platform administrator",
    )
    args = parser.parse_args()
    password = getpass.getpass("Initial password (at least 12 characters): ")
    if password != getpass.getpass("Confirm initial password: "):
        parser.error("passwords do not match")
    asyncio.run(run(args.email, args.display_name, password))


if __name__ == "__main__":
    main()
