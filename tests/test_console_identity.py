"""Human sessions and tenant authorization against disposable PostgreSQL only."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from csa.console.identity import IdentityConfig, IdentityService
from csa.core import db
from csa.core.tenancy import tx
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker

pytestmark = [requires_docker, pytest.mark.asyncio]

IDENTITY_PASSWORD = "synthetic_identity_fixture_pw"


@pytest.fixture
async def identity_pool(migrated_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    """Grant LOGIN only inside the fixture's throwaway testcontainer database."""
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await owner.execute(
            sql.SQL("ALTER ROLE csa_identity LOGIN PASSWORD {}").format(
                sql.Literal(IDENTITY_PASSWORD)
            )
        )
    identity_dsn = make_conninfo(migrated_dsn, user="csa_identity", password=IDENTITY_PASSWORD)
    pool = db.build_pool(identity_dsn, min_size=1, max_size=4, autocommit=True)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


async def test_identity_role_has_no_domain_or_owner_privileges(
    migrated_dsn: str, identity_pool: AsyncConnectionPool
) -> None:
    async with identity_pool.connection() as conn:
        async with conn.cursor(row_factory=dict_row) as cursor:
            await cursor.execute(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = current_user"
            )
            row = await cursor.fetchone()
        assert row is not None and not any(row.values())
        for statement in (
            "SELECT * FROM clients",
            "SELECT * FROM products",
            "SELECT * FROM channel_credentials",
            "SELECT * FROM resolve_endpoint('synthetic-endpoint-123')",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(statement)  # type: ignore[arg-type]


async def test_runtime_role_cannot_read_identity_secrets_or_rewrite_audit(app_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(app_dsn, autocommit=True) as conn:
        for statement in (
            "SELECT * FROM console_users",
            "SELECT * FROM console_sessions",
            "SELECT * FROM console_tokens",
            "SELECT * FROM console_memberships",
            "SELECT * FROM console_login_attempts",
            "UPDATE console_audit SET action = 'tampered'",
            "DELETE FROM console_audit",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(statement)  # type: ignore[arg-type]


async def test_identity_audit_is_append_only_and_rls_is_staged(
    migrated_dsn: str, identity_pool: AsyncConnectionPool
) -> None:
    async with identity_pool.connection() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("UPDATE console_audit SET action = 'tampered'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("DELETE FROM console_audit")
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as conn:
        rows = await (
            await conn.execute(
                "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE relname IN ('console_memberships', 'console_audit')"
            )
        ).fetchall()
    assert len(rows) == 2
    assert all(enabled and not forced for _, enabled, forced in rows)


async def test_membership_and_audit_rows_are_tenant_scoped(
    migrated_dsn: str, identity_pool: AsyncConnectionPool
) -> None:
    tenants = [uuid.uuid4(), uuid.uuid4()]
    user_id = uuid.uuid4()
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        for tenant in tenants:
            await owner.execute(
                "INSERT INTO clients(id, slug, name, platform_version) VALUES (%s,%s,%s,'test')",
                (tenant, f"identity-{tenant}", "Synthetic Identity Tenant"),
            )
        await owner.execute(
            "INSERT INTO console_users(id,email,display_name) VALUES (%s,%s,'Synthetic Member')",
            (user_id, f"{user_id}@example.test"),
        )
        for tenant in tenants:
            await owner.execute(
                "INSERT INTO console_memberships(user_id,client_id,role) "
                "VALUES (%s,%s,'tenant_staff')",
                (user_id, tenant),
            )
            await owner.execute(
                "INSERT INTO console_audit(actor_id,client_id,action,target_id) "
                "VALUES (%s,%s,'fixture','fixture')",
                (user_id, tenant),
            )
    async with tx(identity_pool, str(tenants[0])) as conn:
        memberships = await conn.execute("SELECT client_id FROM console_memberships")
        assert {row["client_id"] for row in await memberships.fetchall()} == {tenants[0]}
        audit = await conn.execute(
            "SELECT client_id FROM console_audit WHERE client_id IS NOT NULL"
        )
        assert {row["client_id"] for row in await audit.fetchall()} == {tenants[0]}
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute(
                "INSERT INTO console_memberships(user_id,client_id,role) "
                "VALUES (%s,%s,'read_only')",
                (uuid.uuid4(), tenants[1]),
            )


async def test_identity_readiness_rejects_owner_and_accidental_domain_grants(
    migrated_dsn: str, identity_pool: AsyncConnectionPool
) -> None:
    config = IdentityConfig(origins=("https://console.example.test",))
    owner_pool = db.build_pool(migrated_dsn, min_size=1, max_size=1, autocommit=True)
    await owner_pool.open(wait=True)
    try:
        with pytest.raises(RuntimeError, match="isolated csa_identity"):
            await IdentityService(owner_pool, config).check_ready()
    finally:
        await owner_pool.close()
    service = IdentityService(identity_pool, config)
    await service.check_ready()
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await owner.execute("GRANT SELECT ON contacts TO csa_identity")
        try:
            with pytest.raises(RuntimeError, match="domain_isolated"):
                await service.check_ready()
        finally:
            await owner.execute("REVOKE SELECT ON contacts FROM csa_identity")
    await service.check_ready()
