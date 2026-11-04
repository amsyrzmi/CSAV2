"""Human sessions and tenant authorization against disposable PostgreSQL only."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any, get_args

import psycopg
import pytest
from csa.console.contracts import ConsoleActor, HrPreset, InviteIn, Role
from csa.console.identity import IdentityConfig, IdentityService
from csa.core import db
from csa.core.tenancy import tx
from csa.hr.access_switches import PresetId
from fastapi import HTTPException
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


class InviteWorld:
    """A synthetic tenant with two tenant admins and a platform admin, on the identity pool."""

    def __init__(self, dsn: str, service: IdentityService, tenant: uuid.UUID) -> None:
        self.dsn = dsn
        self.service = service
        self.tenant = tenant
        self.admins: list[ConsoleActor] = []
        self.platform: ConsoleActor | None = None

    @staticmethod
    def actor(
        user_id: uuid.UUID, *, tenant: uuid.UUID | None, platform: bool = False
    ) -> ConsoleActor:
        return ConsoleActor(
            user_id=user_id,
            email=f"{user_id}@example.test",
            display_name="Synthetic Actor",
            platform_admin=platform,
            must_change_password=False,
            session_id=uuid.uuid4(),
            client_id=None if platform else tenant,
            role=Role.PLATFORM_ADMIN if platform else Role.TENANT_ADMIN,
            memberships=[],
            csrf_hash="synthetic",
        )

    def invite_in(self, **overrides: Any) -> InviteIn:
        fields: dict[str, Any] = {
            "email": f"invitee-{uuid.uuid4()}@example.test",
            "client_id": self.tenant,
            "role": "tenant_staff",
            "display_name": "Synthetic Invitee",
        }
        return InviteIn(**(fields | overrides))

    async def rows(self, query: str, *params: Any) -> list[dict[str, Any]]:
        async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as owner:
            async with owner.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(query, params)  # type: ignore[arg-type]
                return await cursor.fetchall()

    async def token(self, token_id: uuid.UUID) -> dict[str, Any]:
        return (await self.rows("SELECT * FROM console_tokens WHERE id=%s", token_id))[0]


@pytest.fixture
async def invites(
    migrated_dsn: str, identity_pool: AsyncConnectionPool
) -> AsyncIterator[InviteWorld]:
    tenant = uuid.uuid4()
    service = IdentityService(
        identity_pool, IdentityConfig(origins=("https://console.example.test",))
    )
    world = InviteWorld(migrated_dsn, service, tenant)
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await owner.execute(
            "INSERT INTO clients(id, slug, name, platform_version) VALUES (%s,%s,%s,'test')",
            (tenant, f"invites-{tenant}", "Synthetic Invite Tenant"),
        )
        for _ in range(2):
            user_id = uuid.uuid4()
            await owner.execute(
                "INSERT INTO console_users(id,email,display_name,password_hash) "
                "VALUES (%s,%s,'Synthetic Admin','synthetic-hash')",
                (user_id, f"{user_id}@example.test"),
            )
            await owner.execute(
                "INSERT INTO console_memberships(user_id,client_id,role) "
                "VALUES (%s,%s,'tenant_admin')",
                (user_id, tenant),
            )
            world.admins.append(InviteWorld.actor(user_id, tenant=tenant))
        platform_id = uuid.uuid4()
        await owner.execute(
            "INSERT INTO console_users(id,email,display_name,password_hash,is_platform_admin) "
            "VALUES (%s,%s,'Synthetic Platform','synthetic-hash',true)",
            (platform_id, f"{platform_id}@example.test"),
        )
        world.platform = InviteWorld.actor(platform_id, tenant=None, platform=True)
    yield world


async def test_invite_stores_preset_and_chooser(invites: InviteWorld) -> None:
    admin = invites.admins[0]
    issued = await invites.service.invite(admin, invites.invite_in(hr_preset="staff"))
    row = await invites.token(issued.id)
    assert row["hr_preset"] == "staff"
    assert row["hr_preset_chosen_by"] == admin.user_id
    assert row["kind"] == "invite"


async def test_non_owner_cannot_choose_manager(invites: InviteWorld) -> None:
    before_users = await invites.rows("SELECT count(*) AS n FROM console_users")
    before_tokens = await invites.rows("SELECT count(*) AS n FROM console_tokens")
    data = invites.invite_in(hr_preset="manager")
    with pytest.raises(HTTPException) as denied:
        await invites.service.invite(invites.admins[0], data)
    assert denied.value.status_code == 403
    assert denied.value.detail == "hr_preset_requires_hr_owner"
    assert await invites.rows("SELECT count(*) AS n FROM console_users") == before_users
    assert await invites.rows("SELECT count(*) AS n FROM console_tokens") == before_tokens
    assert not await invites.rows("SELECT 1 FROM console_users WHERE email=%s", data.email)


async def test_hr_owner_may_choose_manager(invites: InviteWorld) -> None:
    admin = invites.admins[0]
    issued = await invites.service.invite(
        admin, invites.invite_in(hr_preset="manager"), may_grant_hr=True
    )
    row = await invites.token(issued.id)
    assert row["hr_preset"] == "manager"
    assert row["hr_preset_chosen_by"] == admin.user_id


async def test_platform_admin_may_choose_only_staff(invites: InviteWorld) -> None:
    # The routes pass may_grant_hr=False for every caller until Task 2, and a platform admin
    # never becomes an HR owner, so the service-level rule is what /admin/invites enforces.
    assert invites.platform is not None
    with pytest.raises(HTTPException) as denied:
        await invites.service.invite(invites.platform, invites.invite_in(hr_preset="manager"))
    assert denied.value.status_code == 403
    assert denied.value.detail == "hr_preset_requires_hr_owner"
    issued = await invites.service.invite(invites.platform, invites.invite_in(hr_preset="staff"))
    row = await invites.token(issued.id)
    assert row["hr_preset"] == "staff"
    assert row["hr_preset_chosen_by"] == invites.platform.user_id


async def test_renewal_keeps_preset_and_chooser(invites: InviteWorld) -> None:
    owner, other = invites.admins
    issued = await invites.service.invite(
        owner, invites.invite_in(hr_preset="manager"), may_grant_hr=True
    )
    renewed = await invites.service.renew_invitation(other, issued.id)
    assert renewed.id != issued.id
    row = await invites.token(renewed.id)
    assert row["hr_preset"] == "manager"
    assert row["hr_preset_chosen_by"] == owner.user_id
    assert row["created_by"] == other.user_id
    assert (await invites.token(issued.id))["revoked_at"] is not None


async def test_omitted_preset_stores_none(invites: InviteWorld) -> None:
    issued = await invites.service.invite(invites.admins[0], invites.invite_in())
    row = await invites.token(issued.id)
    assert row["hr_preset"] is None
    assert row["hr_preset_chosen_by"] is None
    renewed = await invites.service.renew_invitation(invites.admins[1], issued.id)
    row = await invites.token(renewed.id)
    assert row["hr_preset"] is None and row["hr_preset_chosen_by"] is None


async def test_listing_returns_the_preset(invites: InviteWorld) -> None:
    issued = await invites.service.invite(
        invites.admins[0], invites.invite_in(hr_preset="hr_team"), may_grant_hr=True
    )
    listed = await invites.service.invitations(invites.admins[0], invites.tenant, 10)
    assert {row["id"]: row["hr_preset"] for row in listed}[issued.id] == "hr_team"


async def test_reset_tokens_cannot_carry_a_preset(invites: InviteWorld) -> None:
    admin = invites.admins[0]
    async with await psycopg.AsyncConnection.connect(invites.dsn, autocommit=True) as owner:
        with pytest.raises(psycopg.errors.CheckViolation):
            await owner.execute(
                "INSERT INTO console_tokens(user_id,kind,token_hash,expires_at,created_by,"
                "hr_preset,hr_preset_chosen_by) "
                "VALUES (%s,'reset',%s,now()+interval '1 hour',%s,'staff',%s)",
                (admin.user_id, "a" * 64, admin.user_id, admin.user_id),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await owner.execute(
                "INSERT INTO console_tokens(user_id,client_id,role,kind,token_hash,expires_at,"
                "created_by,hr_preset) "
                "VALUES (%s,%s,'tenant_staff','invite',%s,now()+interval '1 hour',%s,'staff')",
                (admin.user_id, invites.tenant, "b" * 64, admin.user_id),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await owner.execute(
                "INSERT INTO console_tokens(user_id,client_id,role,kind,token_hash,expires_at,"
                "created_by,hr_preset,hr_preset_chosen_by) "
                "VALUES (%s,%s,'tenant_staff','invite',%s,now()+interval '1 hour',%s,'owner',%s)",
                (admin.user_id, invites.tenant, "c" * 64, admin.user_id, admin.user_id),
            )


async def test_contract_presets_match_the_access_switch_presets() -> None:
    assert get_args(HrPreset) == get_args(PresetId)


async def test_identity_isolation_still_holds(invites: InviteWorld) -> None:
    await invites.service.check_ready()
