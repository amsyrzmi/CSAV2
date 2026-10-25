"""Seed completed local CSA features with synthetic, repeatable dev scenarios."""

from __future__ import annotations

import argparse
import asyncio
import os
from getpass import getpass
from pathlib import Path
from typing import Literal, LiteralString, cast
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from csa.console.security import hash_password
from csa.core import db
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict

from scripts.verify_reset_target import validate_dsn

ROOT = Path(__file__).resolve().parents[1]
Profile = Literal["core", "customer-service", "billing", "hr", "all"]
PROFILES: dict[str, tuple[str, ...]] = {
    "core": (),
    "customer-service": ("customer_service",),
    "billing": ("billing",),
    "hr": ("hr",),
    "all": ("customer_service", "billing", "hr"),
}
TENANTS = (
    (UUID("11111111-1111-1111-1111-111111111111"), "sandbox-amsyar", "a1000000"),
    (UUID("22222222-2222-2222-2222-222222222222"), "sandbox-ihsan", "b2000000"),
)


class SeedReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile: Profile
    counts: dict[str, int]


def seed_id(slug: str, label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"csa:dev-seed:{slug}:{label}")


async def seed(dsn: str, *, profile: Profile = "all", password: str | None = None) -> SeedReport:
    """One transaction, no settings/env-file loading, models or external providers."""
    if os.environ.get("ENV") not in {"dev", "ci"}:
        raise ValueError("Seeder requires explicit ENV=dev or ENV=ci")
    validate_dsn(dsn)
    layers = PROFILES[profile]
    if password is not None and "hr" not in layers:
        raise ValueError("Console logins require the hr or all profile")
    encoded = await hash_password(password) if password is not None else None
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        await conn.execute(db.q.load("seed/lock.sql"))
        cursor = await conn.execute(db.q.load("seed/schema.sql"))
        applied = {row["version"] for row in await cursor.fetchall()}
        required = {path.name.split("_", 1)[0] for path in (ROOT / "db/migrations").glob("*.sql")}
        if required - applied:
            raise ValueError("Seeder requires all checked-out SQL migrations; run migrations first")
        for client_id, slug, _ in TENANTS:
            cursor = await conn.execute(
                db.q.load("seed/tenant.sql"), {"client_id": client_id, "slug": slug}
            )
            rows = await cursor.fetchall()
            if any(row["id"] != client_id or row["slug"] != slug for row in rows):
                raise ValueError("Sandbox tenant ID/slug collision; no seed data committed")
        for filename in ("sandbox_clients.sql", "sandbox_scenarios.sql"):
            query = cast(LiteralString, (ROOT / "db/seed" / filename).read_text())
            await conn.execute(query, prepare=False)
        for client_id, slug, prefix in TENANTS:
            params = {
                "client_id": client_id,
                "slug": slug,
                "contact_id": UUID(f"{prefix}-0000-4000-8000-000000000001"),
                "conversation_id": UUID(f"{prefix}-0000-4000-8000-000000000101"),
                "prefix": "A" if prefix.startswith("a") else "B",
            }
            for layer in layers:
                if layer == "customer_service":
                    from scripts.seed_features import customer_service

                    await customer_service(conn, params)
                elif layer == "billing":
                    from scripts.seed_features import execute_seed

                    await execute_seed(conn, "billing", params)
                elif layer == "hr":
                    from scripts.seed_hr import seed_hr

                    await seed_hr(conn, client_id, slug, encoded)
        cursor = await conn.execute(db.q.load("seed/report.sql"))
        counts = {row["feature"]: row["count"] for row in await cursor.fetchall()}
    return SeedReport(profile=profile, counts=counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="all")
    parser.add_argument("--list", action="store_true", help="List profiles without connecting")
    parser.add_argument(
        "--with-console-logins", action="store_true", help="Prompt for initial demo passwords"
    )
    args = parser.parse_args()
    if args.list:
        for name, layers in PROFILES.items():
            print(f"{name}: core" + (", " + ", ".join(layers) if layers else ""))
        return
    dsn = os.environ.get("SEED_DATABASE_URL")
    if not dsn:
        parser.error("SEED_DATABASE_URL must select the isolated loopback dev owner database")
    password = None
    if args.with_console_logins:
        if args.profile not in {"hr", "all"}:
            parser.error("--with-console-logins requires --profile hr or all")
        password = getpass("Initial synthetic console password (12+ characters): ")
        if password != getpass("Confirm password: "):
            parser.error("Passwords do not match")
    report = asyncio.run(seed(dsn, profile=args.profile, password=password))
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
