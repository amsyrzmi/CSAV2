"""Deliberately add a further CSAV2 platform administrator.

No dotenv files are read. IDENTITY_DATABASE_URL must be explicitly supplied in
this process environment, using the isolated csa_identity login role. Passwords
are read with getpass, never argv, output or source. An existing enabled platform
administrator must approve by re-entering their own password. Invites never
create platform administrators; this command is the only path.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from urllib.parse import urlparse

from csa.console.identity import IdentityConfig, IdentityService
from csa.core.db import build_pool

# Same bounds as csa.console.security.hash_password, checked here at the prompt.
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 1024
PASSWORD_ATTEMPTS = 3


async def run(
    approver_email: str,
    approver_password: str,
    email: str,
    display_name: str,
    password: str,
) -> None:
    dsn = os.environ.get("IDENTITY_DATABASE_URL", "")
    if not dsn or urlparse(dsn).username != "csa_identity":
        raise ValueError("set IDENTITY_DATABASE_URL explicitly using the csa_identity login role")
    pool = build_pool(dsn, min_size=1, max_size=1)
    await pool.open(wait=True)
    try:
        service = IdentityService(pool, IdentityConfig(origins=("http://localhost",)))
        await service.check_ready()
        user_id = await service.add_platform_admin(
            approver_email, approver_password, email, display_name, password
        )
        print(f"Added platform administrator {user_id}; password change required at first login.")
    finally:
        await pool.close()


def read_new_password() -> str | None:
    """Prompt for the initial password; return None after too many bad attempts."""
    for _ in range(PASSWORD_ATTEMPTS):
        password = getpass.getpass(
            f"Initial password (at least {MIN_PASSWORD_LENGTH} characters): "
        )
        if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
            print(
                f"Password must contain between {MIN_PASSWORD_LENGTH} and "
                f"{MAX_PASSWORD_LENGTH} characters. Try again.",
                file=sys.stderr,
            )
        elif password != getpass.getpass("Confirm initial password: "):
            print("Passwords do not match. Try again.", file=sys.stderr)
        else:
            return password
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approver-email", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--add-platform-admin",
        action="store_true",
        required=True,
        help="explicitly acknowledge adding a platform administrator",
    )
    args = parser.parse_args()
    approver_password = getpass.getpass("Approver password: ")
    password = read_new_password()
    if password is None:
        parser.exit(1, "No valid initial password entered; no administrator was added.\n")
    asyncio.run(
        run(args.approver_email, approver_password, args.email, args.display_name, password)
    )


if __name__ == "__main__":
    main()
