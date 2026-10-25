"""Staging seed: exact-target guard, supported-path identity/HR seeding, idempotency."""

from __future__ import annotations

from typing import Any, LiteralString, cast
from uuid import uuid4

import psycopg
import pytest
from csa.console.identity import IdentityConfig, IdentityService
from csa.core import db
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import tuple_row
from scripts import seed_staging

from tests.conftest import requires_docker

IDENTITY_PASSWORD = "seed_identity_pw"  # NON-SECRET disposable fixture password
ADMIN_PASSWORD = "synthetic-admin-password-1"


@pytest.mark.parametrize(
    ("approval", "dsn", "user", "expected"),
    [
        ("approved-isolated-staging", "postgresql://csa@staging-db/csa_staging", "csa", True),
        (
            "approved-isolated-staging",
            "postgres://csa_identity@staging-db:5432/csa_staging",
            "csa_identity",
            True,
        ),
        (None, "postgresql://csa@staging-db/csa_staging", "csa", False),
        ("disabled", "postgresql://csa@staging-db/csa_staging", "csa", False),
        ("approved-isolated-staging", "postgresql://csa@postgres/csa", "csa", False),
        ("approved-isolated-staging", "postgresql://csa_app@staging-db/csa_staging", "csa", False),
        ("approved-isolated-staging", "postgresql://csa@staging-db/csa", "csa", False),
        ("approved-isolated-staging", "postgresql://csa@staging-db:5434/csa_staging", "csa", False),
        (
            "approved-isolated-staging",
            "postgresql://csa@staging-db/csa_staging?host=postgres",
            "csa",
            False,
        ),
    ],
)
def test_seed_requires_approval_and_exact_isolated_target(
    approval: str | None, dsn: str, user: str, expected: bool
) -> None:
    assert seed_staging.approved_target(approval, dsn, user=user) is expected


def test_seed_without_approval_never_reads_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(seed_staging.APPROVAL_ENV, raising=False)

    def forbidden_read(*args: object, **kwargs: object) -> str:
        raise AssertionError("must not read secrets before approval")

    monkeypatch.setattr(seed_staging.Path, "read_text", forbidden_read)
    assert seed_staging.main() == 2


def test_hr_grant_bodies_follow_separation_of_duties() -> None:
    users = {key: uuid4() for key in ("admin", "manager", "staff", "reviewer")}
    bodies = seed_staging.hr_access_bodies(users)
    by_user = {body["user_id"]: body for body in bodies if body["operation"] == "access.save"}
    admin = by_user[str(users["admin"])]
    reviewer = by_user[str(users["reviewer"])]
    reader_absent = all(body["user_id"] != "reader" for body in bodies)
    assert reader_absent
    assert {w["scope"] for w in admin["workflows"] if w["domain"] == "payroll"} == {
        "payroll_operator"
    }
    assert reviewer["workflows"] == [
        {"domain": "payroll", "action": "decide", "scope": "payroll_reviewer"}
    ]
    assert all(g["fields"] == [] for g in admin["grants"] if g["action"] != "directory.read")


async def _count(conn: psycopg.AsyncConnection[Any], query: str, *params: object) -> int:
    async with conn.cursor(row_factory=tuple_row) as cur:
        await cur.execute(cast(LiteralString, query), params)
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


@requires_docker
@pytest.mark.asyncio
async def test_seed_creates_roles_hr_and_records_idempotently(migrated_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await owner.execute(
            sql.SQL("ALTER ROLE csa_identity LOGIN PASSWORD {}").format(
                sql.Literal(IDENTITY_PASSWORD)
            )
        )
    identity_dsn = make_conninfo(migrated_dsn, user="csa_identity", password=IDENTITY_PASSWORD)

    with pytest.raises(RuntimeError, match="platform administrator"):
        await seed_staging.seed(migrated_dsn, identity_dsn)

    pool = db.build_pool(identity_dsn, min_size=1, max_size=1)
    await pool.open(wait=True)
    try:
        service = IdentityService(pool, IdentityConfig(origins=("http://localhost",)))
        await service.bootstrap("owner@platform.example.test", ADMIN_PASSWORD, "Platform Owner")
    finally:
        await pool.close()

    passwords = iter(f"synthetic-seed-password-{n}" for n in range(1, 20))
    report = await seed_staging.seed(
        migrated_dsn, identity_dsn, password_factory=lambda: next(passwords)
    )
    assert report.platform_admin == "owner@platform.example.test"
    assert report.tenants_created == ["demo-meranti", "demo-foreign"]
    assert report.hr_owner_set is True
    assert report.hr_operations == 3 + 8  # three employees, four access saves, four private areas
    assert all(result.created and result.password for result in report.accounts)
    assert len({result.password for result in report.accounts}) == len(report.accounts)

    meranti = next(row for row in report.counts if row["slug"] == "demo-meranti")
    foreign = next(row for row in report.counts if row["slug"] == "demo-foreign")
    assert meranti == {
        "slug": "demo-meranti",
        "members": 5,
        "capabilities_enabled": 9,
        "products": 8,
        "contacts": 2,
        "conversations": 2,
        "messages": 3,
        "orders": 8,
        "tickets": 2,
        "invoices": 2,
        "kb_gaps": 2,
        "hr_employees": 3,
        "hr_grants": 18,
        "hr_workflow_grants": 22,
        "hr_private_grants": 4,
    }
    assert foreign["members"] == 1 and foreign["capabilities_enabled"] == 0
    assert foreign["products"] == 1 and foreign["orders"] == 1 and foreign["messages"] == 1

    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        roles = {
            row[0]: row[1]
            for row in await (
                await conn.execute(
                    "SELECT u.email, m.role FROM console_memberships m "
                    "JOIN console_users u ON u.id = m.user_id WHERE m.client_id = %s",
                    (seed_staging.MERANTI,),
                )
            ).fetchall()
        }
        assert roles == {
            "admin@meranti.example.test": "tenant_admin",
            "nadia@meranti.example.test": "tenant_staff",
            "amir@meranti.example.test": "tenant_staff",
            "reader@meranti.example.test": "read_only",
            "reviewer@meranti.example.test": "tenant_admin",
        }
        seeded = [result.user_id for result in report.accounts]
        assert await _count(
            conn,
            "SELECT count(*) FROM console_users WHERE id = ANY(%s) AND must_change_password",
            seeded,
        ) == len(seeded)
        assert (
            await _count(
                conn,
                "SELECT count(*) FROM console_sessions "
                "WHERE user_id = ANY(%s) AND revoked_at IS NULL",
                seeded,
            )
            == 0
        )
        assert await _count(conn, "SELECT count(*) FROM capability_registry") == 18
        packages = await (
            await conn.execute(
                "SELECT client_id, package, status, reference, activated_by IS NOT NULL, "
                "activated_at IS NOT NULL FROM console_service_packages ORDER BY package"
            )
        ).fetchall()
        assert {row[1] for row in packages if row[0] == seed_staging.MERANTI} == {
            "customer_support",
            "orders_products",
            "subscriptions",
            "bookings",
            "marketplaces",
            "hr",
        }
        assert all(row[2:] == ("active", "staging-seed-v1", True, True) for row in packages)
        assert not [row for row in packages if row[0] == seed_staging.FOREIGN]
        events = await _count(conn, "SELECT count(*) FROM hr_authority_events")
        assert events == report.hr_operations
        assert await _count(conn, "SELECT count(*) FROM hr_employee_accounts") == 3
        assert (
            await _count(conn, "SELECT count(*) FROM console_audit WHERE action = 'tenant.created'")
            == 2
        )
        assert await _count(
            conn,
            "SELECT count(*) FROM console_audit WHERE action IN "
            "('identity.invited', 'identity.invite_accepted')",
        ) == 2 * len(seeded)
        assert await _count(conn, "SELECT count(*) FROM order_items") == 8
        assert await _count(conn, "SELECT count(*) FROM kb_documents") == 0

    again = await seed_staging.seed(migrated_dsn, identity_dsn, password_factory=lambda: "unused")
    assert again.tenants_created == []
    assert again.hr_owner_set is False
    assert all(not result.created and result.password is None for result in again.accounts)
    assert again.counts == report.counts
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        assert await _count(conn, "SELECT count(*) FROM order_items") == 8
        assert await _count(conn, "SELECT count(*) FROM hr_employees") == 3
        assert await _count(conn, "SELECT count(*) FROM console_service_packages") == 6
