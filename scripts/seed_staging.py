"""Seed the isolated staging console with synthetic tenants, roles and records.

Owner-approved, additive and idempotent. No reset, delete, provider call,
worker, KB vector, delivery or runtime trace is produced.

* Tenants are onboarded with the same SQL the console onboarding route uses,
  including the nine capability rows and an attributed audit entry.
* Accounts are created through :class:`IdentityService` (invite then accept) as
  the existing platform administrator, so every role lands in
  ``console_memberships`` with normal identity audit. Generated passwords are
  printed once; every seeded account must change its password at first login.
* HR ownership uses the HR owner bootstrap SQL; employees and grants go through
  ``public.hr_admin_apply`` so receipts and authority events are genuine.
* Business records come from ``db/sql/seed_staging/records.sql``.

Connection strings are read from the mounted secret files only after explicit
approval and only for the exact isolated staging database.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import psycopg
from csa.console.contracts import ConsoleActor, InviteIn
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.onboarding import _AGENTS as ONBOARDED_AGENTS
from csa.console.packages import PACKAGES
from csa.core import db
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

APPROVAL = "approved-isolated-staging"
APPROVAL_ENV = "CSA_STAGING_SEED_APPROVAL"
SECRET_DIR = Path("/run/secrets")
OWNER_SECRET_NAME = "STAGING_OWNER_DSN"
IDENTITY_SECRET_NAME = "IDENTITY_DATABASE_URL"
SEED_REFERENCE = "staging-seed-v1"
LOCK_KEY = 7_420_026

MERANTI = UUID("a1000000-0000-4000-8000-000000000001")
FOREIGN = UUID("a1000000-0000-4000-8000-000000000002")
EMPLOYEE_MANAGER = UUID("e1000000-0000-4000-8000-000000000001")
EMPLOYEE_STAFF = UUID("e1000000-0000-4000-8000-000000000002")
EMPLOYEE_REVIEWER = UUID("e1000000-0000-4000-8000-000000000003")
DIRECTORY_FIELDS = ["display_name", "job_title", "department", "work_email", "assigned_manager"]


@dataclass(frozen=True)
class TenantSpec:
    id: UUID
    slug: str
    name: str
    brand_name: str
    languages: list[str]
    enable_capabilities: bool


@dataclass(frozen=True)
class AccountSpec:
    key: str
    tenant: UUID
    email: str
    display_name: str
    role: str


TENANTS: tuple[TenantSpec, ...] = (
    TenantSpec(
        MERANTI,
        "demo-meranti",
        "Meranti Gadget Lab (synthetic)",
        "Meranti Gadget Lab",
        ["en", "ms"],
        True,
    ),
    TenantSpec(
        FOREIGN, "demo-foreign", "Foreign Tenant (synthetic)", "Foreign Demo", ["en"], False
    ),
)

ACCOUNTS: tuple[AccountSpec, ...] = (
    AccountSpec(
        "admin", MERANTI, "admin@meranti.example.test", "Demo Tenant Admin", "tenant_admin"
    ),
    AccountSpec(
        "manager", MERANTI, "nadia@meranti.example.test", "Demo Nadia Manager", "tenant_staff"
    ),
    AccountSpec("staff", MERANTI, "amir@meranti.example.test", "Demo Amir Support", "tenant_staff"),
    AccountSpec("reader", MERANTI, "reader@meranti.example.test", "Demo Reader", "read_only"),
    AccountSpec(
        "reviewer",
        MERANTI,
        "reviewer@meranti.example.test",
        "Demo Independent Reviewer",
        "tenant_admin",
    ),
    AccountSpec(
        "foreign_admin", FOREIGN, "admin@foreign.example.test", "Demo Foreign Admin", "tenant_admin"
    ),
)


@dataclass
class AccountResult:
    spec: AccountSpec
    user_id: UUID
    created: bool
    password: str | None


@dataclass
class SeedReport:
    platform_admin: str
    accounts: list[AccountResult] = field(default_factory=list)
    tenants_created: list[str] = field(default_factory=list)
    hr_owner_set: bool = False
    hr_operations: int = 0
    counts: list[dict[str, Any]] = field(default_factory=list)


def approved_target(approval: str | None, dsn: str, *, user: str) -> bool:
    """Only the exact isolated staging database with the expected role."""
    url = urlsplit(dsn)
    return (
        approval == APPROVAL
        and url.scheme in {"postgres", "postgresql"}
        and url.hostname == "staging-db"
        and url.port in {None, 5432}
        and url.username == user
        and url.path == "/csa_staging"
        and not url.query
        and not url.fragment
    )


def generate_password() -> str:
    return secrets.token_urlsafe(18)


def _directory(scope: str) -> dict[str, Any]:
    return {"action": "directory.read", "scope": scope, "fields": DIRECTORY_FIELDS}


def _grant(action: str, scope: str) -> dict[str, Any]:
    return {"action": action, "scope": scope, "fields": []}


def _workflow(domain: str, action: str, scope: str) -> dict[str, str]:
    return {"domain": domain, "action": action, "scope": scope}


def hr_access_bodies(users: dict[str, UUID]) -> list[dict[str, Any]]:
    """Role-appropriate HR grants: owner, manager, staff, independent reviewer."""
    self_training = [
        _grant("training.read", "self"),
        _grant("training.draft", "self"),
        _grant("training.submit", "self"),
        _grant("training.withdraw", "self"),
    ]
    self_workflows = [
        _workflow("attendance", "read", "self"),
        _workflow("attendance", "prepare", "self"),
        _workflow("leave", "read", "self"),
        _workflow("leave", "prepare", "self"),
    ]
    private_areas = (
        ("admin", "recruitment"),
        ("admin", "attendance"),
        ("admin", "payroll"),
        ("reviewer", "payroll"),
    )
    return [
        {
            "operation": "access.save",
            "user_id": str(users["admin"]),
            "grants": [
                _directory("hr_operator"),
                _grant("training.read", "hr_operator"),
                _grant("training.decide", "client_owner"),
            ],
            "workflows": [
                _workflow("recruitment", "read", "client_owner"),
                _workflow("recruitment", "prepare", "client_owner"),
                _workflow("recruitment", "decide", "client_owner"),
                _workflow("attendance", "read", "client_owner"),
                _workflow("attendance", "decide", "client_owner"),
                _workflow("leave", "read", "client_owner"),
                _workflow("leave", "decide", "client_owner"),
                _workflow("payroll", "read", "payroll_operator"),
                _workflow("payroll", "prepare", "payroll_operator"),
            ],
        },
        {
            "operation": "access.save",
            "user_id": str(users["manager"]),
            "grants": [
                _directory("self"),
                *self_training,
                _directory("assigned_manager"),
                _grant("training.read", "assigned_manager"),
                _grant("training.decide", "assigned_manager"),
            ],
            "workflows": [
                *self_workflows,
                _workflow("attendance", "read", "assigned_manager"),
                _workflow("attendance", "decide", "assigned_manager"),
                _workflow("leave", "read", "assigned_manager"),
                _workflow("leave", "decide", "assigned_manager"),
            ],
        },
        {
            "operation": "access.save",
            "user_id": str(users["staff"]),
            "grants": [_directory("self"), *self_training],
            "workflows": self_workflows,
        },
        {
            "operation": "access.save",
            "user_id": str(users["reviewer"]),
            "grants": [_directory("hr_operator"), _grant("training.read", "hr_operator")],
            "workflows": [_workflow("payroll", "decide", "payroll_reviewer")],
        },
        *(
            {
                "operation": "private_access.save",
                "user_id": str(users[key]),
                "area": area,
                "enabled": True,
            }
            for key, area in private_areas
        ),
    ]


def _employee(
    employee: UUID,
    name: str,
    title: str,
    department: str,
    email: str,
    manager: UUID | None,
    account: UUID,
) -> dict[str, Any]:
    return {
        "operation": "employee.save",
        "employee_id": str(employee),
        "values": {
            "display_name": name,
            "job_title": title,
            "department": department,
            "work_email": email,
            "assigned_manager": (
                {"employee_id": str(manager), "tenant_id": str(MERANTI)} if manager else None
            ),
        },
        "account_id": str(account),
        "active": True,
        "region": "peninsular_labuan",
        "legal_entity_reference": "MGL-SYNTHETIC",
        "employment_category": "full_time",
    }


def hr_employee_bodies(users: dict[str, UUID]) -> list[tuple[UUID, dict[str, Any]]]:
    return [
        (
            EMPLOYEE_MANAGER,
            _employee(
                EMPLOYEE_MANAGER,
                "Demo Nadia Manager",
                "Support Lead",
                "Customer Support",
                "nadia@meranti.example.test",
                None,
                users["manager"],
            ),
        ),
        (
            EMPLOYEE_STAFF,
            _employee(
                EMPLOYEE_STAFF,
                "Demo Amir Support",
                "Support Specialist",
                "Customer Support",
                "amir@meranti.example.test",
                EMPLOYEE_MANAGER,
                users["staff"],
            ),
        ),
        (
            EMPLOYEE_REVIEWER,
            _employee(
                EMPLOYEE_REVIEWER,
                "Demo Independent Reviewer",
                "Finance Reviewer",
                "Finance",
                "reviewer@meranti.example.test",
                None,
                users["reviewer"],
            ),
        ),
    ]


def _fingerprint(body: dict[str, Any]) -> str:
    """Canonical SHA-256 of the operation body, matching the HR operations service."""
    payload = json.loads(json.dumps(body))
    for grant in payload.get("grants", []):
        grant["fields"] = sorted(grant["fields"])
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _actor(admin: dict[str, Any]) -> ConsoleActor:
    """A platform-admin actor for service calls; the session id is never persisted."""
    return ConsoleActor(
        user_id=admin["id"],
        email=admin["email"],
        display_name=admin["display_name"],
        platform_admin=True,
        must_change_password=False,
        session_id=uuid4(),
        client_id=None,
        role=None,
        memberships=[],
        csrf_hash="0" * 64,
    )


def seeded_packages() -> list[str]:
    """Packages whose member capabilities the seed enables in full.

    Packages with a member the onboarding SQL does not provision (e_invoicing) are
    skipped. HR and Staff Email have no capabilities; the seed never enables HR module
    settings or ``email_settings``, so they are not activated.
    """
    onboarded = {agent_class().name for agent_class in ONBOARDED_AGENTS}
    return [p.key for p in PACKAGES if p.capabilities and set(p.capabilities) <= onboarded]


async def _ensure_tenant(
    conn: psycopg.AsyncConnection[Any], spec: TenantSpec, admin_id: UUID, report: SeedReport
) -> None:
    row = await db.q.fetch_one("seed_staging/client_by_slug.sql", (spec.slug,), conn=conn)
    if row is not None and row["id"] != spec.id:
        raise RuntimeError(f"tenant slug {spec.slug} exists with a different id; refusing to seed")
    if row is None:
        await db.q.fetch_one(
            "console/client_create.sql",
            (spec.id, spec.slug, spec.name, "Asia/Kuala_Lumpur"),
            conn=conn,
        )
        for agent_class in ONBOARDED_AGENTS:
            agent = agent_class()
            await db.q.execute(
                "console/provision_capabilities.sql",
                (
                    spec.id,
                    agent.name,
                    agent.name.removesuffix("_agent").replace("_", " ").title(),
                    "CSAV2 capability; enable deliberately after configuration",
                    f"python:{agent.name}",
                    Jsonb(agent.Input.model_json_schema()),
                ),
                conn=conn,
            )
        await db.q.execute(
            "console/audit.sql",
            (
                admin_id,
                spec.id,
                "tenant.created",
                str(spec.id),
                Jsonb(
                    {
                        "slug": spec.slug,
                        "capabilities_enabled": spec.enable_capabilities,
                        "seed": SEED_REFERENCE,
                    }
                ),
            ),
            conn=conn,
        )
        report.tenants_created.append(spec.slug)
    await db.q.execute(
        "seed_staging/tenant_profile.sql",
        (spec.brand_name, "MY", spec.languages, spec.id),
        conn=conn,
    )
    if spec.enable_capabilities:
        await db.q.execute(
            "seed_staging/capability_enable.sql", (SEED_REFERENCE, spec.id), conn=conn
        )
        await db.q.execute(
            "seed_staging/service_packages_activate.sql",
            (spec.id, admin_id, SEED_REFERENCE, seeded_packages()),
            conn=conn,
        )


async def _ensure_account(
    owner: psycopg.AsyncConnection[Any],
    identity: IdentityService,
    actor: ConsoleActor,
    spec: AccountSpec,
    password_factory: Callable[[], str],
) -> AccountResult:
    existing = await db.q.fetch_one("seed_staging/user_by_email.sql", (spec.email,), conn=owner)
    if existing is not None:
        membership = await db.q.fetch_one(
            "seed_staging/membership.sql", (existing["id"], spec.tenant), conn=owner
        )
        if membership is None:
            raise RuntimeError(
                f"{spec.email} already exists without membership in the seed tenant; "
                "resolve through member administration before seeding"
            )
        return AccountResult(spec, existing["id"], created=False, password=None)
    issued = await identity.invite(
        actor,
        InviteIn.model_validate(
            {
                "email": spec.email,
                "client_id": spec.tenant,
                "role": spec.role,
                "display_name": spec.display_name,
            }
        ),
    )
    password = password_factory()
    accepted, _session, _csrf = await identity.accept_token(issued.token, password)
    return AccountResult(spec, accepted.user_id, created=True, password=password)


async def _seed_hr(
    conn: psycopg.AsyncConnection[Any], admin_id: UUID, users: dict[str, UUID], report: SeedReport
) -> None:
    owner_row = await db.q.fetch_one("seed_staging/hr_owner.sql", (MERANTI,), conn=conn)
    if owner_row is None:
        created = await db.q.fetch_one(
            "hr/bootstrap_owner.sql", (admin_id, SEED_REFERENCE, MERANTI, users["admin"]), conn=conn
        )
        if created is None:
            raise RuntimeError("HR owner bootstrap refused; tenant admin membership not eligible")
        report.hr_owner_set = True
    elif owner_row["user_id"] != users["admin"]:
        raise RuntimeError("HR owner already assigned to another account; refusing to override")
    await db.q.execute(
        "seed_staging/hr_context.sql", (str(MERANTI), str(users["admin"])), conn=conn
    )
    for employee_id, body in hr_employee_bodies(users):
        exists = await db.q.fetch_one(
            "seed_staging/hr_employee_exists.sql", (MERANTI, employee_id), conn=conn
        )
        if exists is not None:
            continue
        await db.q.fetch_one(
            "hr/admin_apply.sql", (MERANTI, uuid4(), _fingerprint(body), Jsonb(body)), conn=conn
        )
        report.hr_operations += 1
    for body in hr_access_bodies(users):
        await db.q.fetch_one(
            "hr/admin_apply.sql", (MERANTI, uuid4(), _fingerprint(body), Jsonb(body)), conn=conn
        )
        report.hr_operations += 1


async def seed(
    owner_dsn: str,
    identity_dsn: str,
    *,
    password_factory: Callable[[], str] = generate_password,
) -> SeedReport:
    owner_pool = db.build_pool(owner_dsn, min_size=1, max_size=2)
    identity_pool = db.build_pool(identity_dsn, min_size=1, max_size=1)
    await owner_pool.open(wait=True)
    await identity_pool.open(wait=True)
    try:
        identity = IdentityService(identity_pool, IdentityConfig(origins=("http://localhost",)))
        await identity.check_ready()
        async with owner_pool.connection() as lock_conn:
            await lock_conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            try:
                return await _seed_locked(owner_pool, identity, password_factory)
            finally:
                await lock_conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    finally:
        await identity_pool.close()
        await owner_pool.close()


async def _seed_locked(
    owner_pool: AsyncConnectionPool,
    identity: IdentityService,
    password_factory: Callable[[], str],
) -> SeedReport:
    async with owner_pool.connection() as conn:
        admin = await db.q.fetch_one("seed_staging/platform_admin.sql", conn=conn)
    if admin is None:
        raise RuntimeError("no enabled platform administrator; run the admin bootstrap first")
    report = SeedReport(platform_admin=admin["email"])
    actor = _actor(admin)

    async with owner_pool.connection() as conn, conn.transaction():
        for spec in TENANTS:
            await _ensure_tenant(conn, spec, admin["id"], report)

    async with owner_pool.connection() as conn:
        for spec in ACCOUNTS:
            report.accounts.append(
                await _ensure_account(conn, identity, actor, spec, password_factory)
            )
    users = {result.spec.key: result.user_id for result in report.accounts}

    async with owner_pool.connection() as conn, conn.transaction():
        await _seed_hr(conn, admin["id"], users, report)

    created_ids = [result.user_id for result in report.accounts if result.created]
    async with owner_pool.connection() as conn, conn.transaction():
        if created_ids:
            await db.q.execute("seed_staging/force_password_change.sql", (created_ids,), conn=conn)
            await db.q.execute("seed_staging/revoke_sessions.sql", (created_ids,), conn=conn)
        await conn.execute(db.q.load("seed_staging/records.sql"))
        report.counts = await db.q.fetch_all(
            "seed_staging/report.sql", ([spec.id for spec in TENANTS],), conn=conn
        )
    return report


def render(report: SeedReport) -> str:
    lines = [
        f"Seeded as platform administrator {report.platform_admin}.",
        f"Tenants created: {', '.join(report.tenants_created) or 'none (already present)'}.",
        f"HR owner assigned: {report.hr_owner_set}; "
        f"HR operations recorded: {report.hr_operations}.",
        "",
        "Accounts (passwords shown once; each account must change it at first login):",
    ]
    for result in report.accounts:
        state = result.password if result.created else "(existing account, unchanged)"
        lines.append(f"  {result.spec.role:<13} {result.spec.email:<32} {state}")
    lines.append("")
    for row in report.counts:
        summary = ", ".join(f"{key}={value}" for key, value in row.items() if key != "slug")
        lines.append(f"{row['slug']}: {summary}")
    return "\n".join(lines)


def main() -> int:
    if os.environ.get(APPROVAL_ENV) != APPROVAL:
        print("Explicit isolated-staging seed approval required", file=sys.stderr)
        return 2
    owner_dsn = (SECRET_DIR / OWNER_SECRET_NAME).read_text().strip()
    identity_dsn = (SECRET_DIR / IDENTITY_SECRET_NAME).read_text().strip()
    if not approved_target(os.environ[APPROVAL_ENV], owner_dsn, user="csa"):
        print("Owner target must be csa on the isolated staging database", file=sys.stderr)
        return 2
    if not approved_target(os.environ[APPROVAL_ENV], identity_dsn, user="csa_identity"):
        print(
            "Identity target must be csa_identity on the isolated staging database",
            file=sys.stderr,
        )
        return 2
    report = asyncio.run(seed(owner_dsn, identity_dsn))
    print(render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
