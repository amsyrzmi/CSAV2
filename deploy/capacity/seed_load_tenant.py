"""Create one synthetic tenant with a Telegram webhook endpoint for the capacity gate.

Mirrors the ``ingress_context`` fixture in tests/test_ingress.py. Needs the owner connection
(``/run/secrets/owner_dsn``) and the runtime credentials key (``/run/secrets/CREDENTIALS_KEY``),
both mounted read-only by the README's command. Prints ``public_id`` and ``secret`` as JSON; the
secret is synthetic and protects nothing.

The tenant has a recognisable slug (``capacity-gate-...``). It holds no real customer data.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

# Settings needs these to load; none is used to reach anything.
for name, value in {
    "ENV": "ci",
    "DATABASE_URL": "postgresql://unused:unused@localhost:5432/unused",
    "LG_DATABASE_URL": "postgresql://unused:unused@localhost:5432/unused",
    "GROQ_API_KEY": "unused",
    "PUBLIC_BASE_URL": "https://example.test",
}.items():
    os.environ.setdefault(name, value)

from csa.channels.ingress import TELEGRAM_WEBHOOK_SECRET_META_KEY  # noqa: E402
from csa.core import crypto  # noqa: E402

SECRET = "capacity-gate-secret"


def main() -> None:
    """Insert the tenant, its credential and its endpoint, and print how to reach it."""
    owner_dsn = Path("/run/secrets/owner_dsn").read_text(encoding="utf-8").strip()
    client_id = str(uuid.uuid4())
    credential_id = str(uuid.uuid4())
    public_id = f"telegram-{uuid.uuid4().hex[:20]}"
    secret_enc = crypto.encrypt(
        SECRET,
        crypto.credential_aad(
            "channel_credentials", "meta.telegram_webhook_secret_enc", credential_id
        ),
    )
    token_enc = crypto.encrypt(
        "0:capacity-gate-not-a-real-token",
        crypto.credential_aad("channel_credentials", "access_token", credential_id),
    )
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
            (client_id, f"capacity-gate-{client_id[:8]}", "Capacity gate", "capacity-gate"),
        )
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, access_token, meta) "
            "VALUES (%s, %s, 'telegram', %s, %s)",
            (
                credential_id,
                client_id,
                token_enc,
                Jsonb({TELEGRAM_WEBHOOK_SECRET_META_KEY: secret_enc}),
            ),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints "
            "(id, client_id, public_id, credential_id, rate_limit_rpm) "
            "VALUES (%s, %s, %s, %s, 6000)",
            (str(uuid.uuid4()), client_id, public_id, credential_id),
        )
    print(json.dumps({"client_id": client_id, "public_id": public_id, "secret": SECRET}))


if __name__ == "__main__":
    main()
