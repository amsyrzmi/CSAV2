"""The staging reset's company step: companies, packages, modes, profiles and invitations.

Runs inside the staging gateway container, because only the gateway can send mail. It uses the
console's own code, as the first platform admin of the roster, and only ever adds: a second run
changes nothing and sends nothing. The password and the roster arrive on stdin, one line of
password and then the roster as JSON. Output lines name companies, packages and members by
address; a stop message names a roster position. No output carries a link or a token.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from csa.console.auth import invitation_notify, invite_facts
from csa.console.contracts import ConsoleActor, InviteIn
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.mailer import Mailer
from csa.console.onboarding import ClientIn, create_client
from csa.console.services import ActivateIn, activate_package
from csa.core import db
from csa.core.settings import get_settings
from csa.core.tenancy import tx
from csa.ops.runbooks import set_deployment_mode
from fastapi import HTTPException
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from scripts.staging_roster import Company, Member, Roster, RosterError, parse_roster

STAGING_HOST = "staging-db"
STAGING_DATABASE = "/csa_staging"
PACKAGE_REFERENCE = "staging-reset"
LISTING_LIMIT = 1000
# Background jobs visit only pilot and active companies (jobs/list_active_clients.sql).
STAGING_STATUS = "pilot"

_MAIL_OFF = (
    "mail is not switched on in this gateway; the accounts step sends the invitations, "
    "so it creates nothing until mail is on"
)
_ADMIN_REFUSED = (
    "the first platform admin of the roster was not accepted: check the password, and if "
    "this admin has never signed in, sign in and change the password first. No company was created"
)


class AccountsStop(Exception):
    """The step refuses to go on. The text never includes an address, a link or a token."""


def approved_target(dsn: str, *, user: str) -> bool:
    """Only the isolated staging database, as the expected role."""
    url = urlsplit(dsn)
    return (
        url.scheme in {"postgres", "postgresql"}
        and url.hostname == STAGING_HOST
        and url.port in {None, 5432}
        and url.username == user
        and url.path == STAGING_DATABASE
        and not url.query
        and not url.fragment
    )


async def _verified_actor(identity: IdentityService, roster: Roster, password: str) -> ConsoleActor:
    try:
        return await identity.verify_platform_admin(roster.platform_admins[0].email, password)
    except PermissionError:
        raise AccountsStop(_ADMIN_REFUSED) from None
    except HTTPException as exc:
        if exc.status_code == 429:
            raise AccountsStop(
                "too many password attempts; wait up to 15 minutes and try again"
            ) from None
        raise


async def _existing_companies(
    pool: AsyncConnectionPool, roster: Roster
) -> dict[str, dict[str, Any]]:
    """The companies already on staging, keyed by slug; any company outside the roster stops."""
    async with pool.connection() as conn:
        rows = await db.q.fetch_all("console/client_list.sql", conn=conn)
    wanted = {company.slug: company for company in roster.companies}
    for row in rows:
        company = wanted.get(row["slug"])
        if company is None:
            raise AccountsStop(
                f"company {row['slug']} is not in the roster; this staging was not wiped. "
                "Nothing was created"
            )
        if row["name"] != company.name:
            raise AccountsStop(
                f"company {row['slug']} exists with a different name than the roster. "
                "Nothing was created"
            )
    return {row["slug"]: row for row in rows}


def _place(index: int, member_index: int) -> str:
    return f"companies[{index}].members[{member_index}]"


async def _members_there(
    identity: IdentityService, actor: ConsoleActor, client_id: UUID
) -> dict[tuple[str, str], str]:
    """(address, role) -> "member", "pending", "expired" or "revoked" for one company.

    An accepted invitation is skipped: the account is a member under its current role, which
    may have changed since. Of the rest, the newest invitation per address and role decides.
    """
    there: dict[tuple[str, str], str] = {}
    now = datetime.now(UTC)
    for row in await identity.invitations(actor, client_id, LISTING_LIMIT):  # newest first
        key = (row["email"], row["role"])
        if row["kind"] != "invite" or row["consumed_at"] is not None or key in there:
            continue
        if row["revoked_at"] is not None:
            there[key] = "revoked"
        elif row["expires_at"] <= now:
            there[key] = "expired"
        else:
            there[key] = "pending"
    for row in await identity.members(actor, client_id):
        there[(row["email"], row["role"])] = "member"
    return there


async def _check_members(
    identity: IdentityService,
    actor: ConsoleActor,
    roster: Roster,
    existing: dict[str, dict[str, Any]],
) -> dict[str, dict[tuple[str, str], str]]:
    """Check every member before anything is written; returns who is already there."""
    found: dict[str, dict[tuple[str, str], str]] = {}
    for c_index, company in enumerate(roster.companies):
        row = existing.get(company.slug)
        there = await _members_there(identity, actor, row["id"]) if row else {}
        found[company.slug] = there
        for m_index, member in enumerate(company.members):
            if (member.email, member.role) in there:
                continue
            if await identity.account_exists(member.email):
                raise AccountsStop(
                    f"{_place(c_index, m_index)} already has an account that is not a "
                    f"{member.role} or an invitee of {company.slug}. Nothing was created"
                )
    return found


async def _ensure_company(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    company: Company,
    existing: dict[str, dict[str, Any]],
    out: Callable[[str], None],
) -> UUID:
    row = existing.get(company.slug)
    if row is not None:
        out(f"{company.slug}: company: already there")
        return row["id"]
    created = await create_client(
        pool, actor, ClientIn(slug=company.slug, name=company.name, timezone=company.timezone)
    )
    out(f"{company.slug}: company: created")
    return created.id


async def _ensure_packages(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    company: Company,
    client_id: UUID,
    out: Callable[[str], None],
) -> None:
    async with tx(pool, str(client_id)) as conn:
        rows = await db.q.fetch_all("console/service_packages_list.sql", (client_id,), conn=conn)
    active = {row["package"] for row in rows if row["status"] == "active"}
    for key in company.ordered_packages():
        if key in active:
            out(f"{company.slug}: package {key}: already there")
            continue
        await activate_package(
            pool, client_id, key, ActivateIn(reference=PACKAGE_REFERENCE), actor=actor
        )
        out(f"{company.slug}: package {key}: created")


async def _ensure_mode(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    company: Company,
    client_id: UUID,
    out: Callable[[str], None],
) -> None:
    mode = company.deployment_mode
    if mode is None:
        return
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("console/client_deployment_mode.sql", (client_id,), conn=conn)
        if row is not None and row["deployment_mode"] == mode and row["policy_mode"] == mode:
            out(f"{company.slug}: deployment mode {mode}: already there")
            return
        previous = row["policy_mode"] if row else None
        await set_deployment_mode(conn, client_id, mode)
        await db.q.execute(
            "console/audit.sql",
            (
                actor.user_id,
                client_id,
                "tenant.deployment_mode_set",
                str(client_id),
                Jsonb({"deployment_mode": mode, "previous": previous, "source": PACKAGE_REFERENCE}),
            ),
            conn=conn,
        )
    out(f"{company.slug}: deployment mode {mode}: created")


async def _ensure_profile(
    pool: AsyncConnectionPool,
    actor: ConsoleActor,
    company: Company,
    client_id: UUID,
    out: Callable[[str], None],
) -> None:
    """Set what a company made through the console lacks, or background work never visits it."""
    wanted = {"status": STAGING_STATUS, "brand_name": company.name}
    async with tx(pool, str(client_id)) as conn:
        row = await db.q.fetch_one("console/client_staging_profile.sql", (client_id,), conn=conn)
        assert row is not None  # _ensure_company has just found or made it
        changed = [field for field, value in wanted.items() if row[field] != value]
        if changed:
            await db.q.execute(
                "console/client_staging_profile_set.sql",
                (STAGING_STATUS, company.name, client_id),
                conn=conn,
            )
            await db.q.execute(
                "console/audit.sql",
                (
                    actor.user_id,
                    client_id,
                    "tenant.staging_profile_set",
                    str(client_id),
                    Jsonb(
                        {
                            "fields": changed,
                            **wanted,
                            "previous": {field: row[field] for field in wanted},
                            "source": PACKAGE_REFERENCE,
                        }
                    ),
                ),
                conn=conn,
            )
    for field, label in (("status", "status"), ("brand_name", "brand name")):
        result = "created" if field in changed else "already there"
        out(f"{company.slug}: {label} {wanted[field]}: {result}")


async def _invite_members(
    pool: AsyncConnectionPool,
    identity: IdentityService,
    mailer: Mailer,
    actor: ConsoleActor,
    company: Company,
    client_id: UUID,
    there: dict[tuple[str, str], str],
    out: Callable[[str], None],
) -> int:
    """Invite each member who is not there yet; returns how many invitations were not mailed."""
    unsent = 0
    for m_index, member in enumerate(company.members):
        label = f"{company.slug}: members[{m_index}] ({member.role}) {member.email}"
        state = there.get((member.email, member.role))
        if state == "member":
            out(f"{label}: already there")
        elif state == "pending":
            out(
                f"{label}: already there (invitation pending; "
                "if its mail was never sent, resend it from the Team page)"
            )
        elif state in ("expired", "revoked"):
            out(
                f"{label}: already there (invitation {state}; "
                "use Renew invitation on the Team page)"
            )
        elif await _invite(pool, identity, mailer, actor, member, client_id) == "email":
            out(f"{label}: invited, mail sent")
        else:
            unsent += 1
            out(f"{label}: invited, mail not sent (resend it from the Team page)")
    return unsent


async def _invite(
    pool: AsyncConnectionPool,
    identity: IdentityService,
    mailer: Mailer,
    actor: ConsoleActor,
    member: Member,
    client_id: UUID,
) -> str:
    """Invite one member and return the delivery. A returned token is dropped, never shown."""
    facts = await invite_facts(pool, actor, client_id, mail_on=True)
    issued = await identity.invite(
        actor,
        InviteIn(
            email=member.email,
            client_id=client_id,
            role=member.role,
            display_name=member.display_name,
        ),
        may_grant_hr=False,
        notify=invitation_notify(
            mailer, actor, facts, to=member.email, invitee_name=member.display_name
        ),
    )
    return issued.delivery


async def run(
    pool: AsyncConnectionPool,
    identity: IdentityService,
    mailer: Mailer | None,
    password: str,
    roster: Roster,
    out: Callable[[str], None],
) -> int:
    """Create what the roster lists. Returns 0, or 2 when an invitation was not mailed."""
    if mailer is None or not mailer.enabled:
        raise AccountsStop(_MAIL_OFF)
    actor = await _verified_actor(identity, roster, password)
    existing = await _existing_companies(pool, roster)
    there = await _check_members(identity, actor, roster, existing)
    unsent = 0
    for company in roster.companies:
        try:
            client_id = await _ensure_company(pool, actor, company, existing, out)
            await _ensure_packages(pool, actor, company, client_id, out)
            await _ensure_mode(pool, actor, company, client_id, out)
            await _ensure_profile(pool, actor, company, client_id, out)
            unsent += await _invite_members(
                pool, identity, mailer, actor, company, client_id, there[company.slug], out
            )
        except HTTPException as exc:
            raise AccountsStop(f"{company.slug}: stopped: {exc.detail}") from None
    if unsent:
        out(f"{unsent} invitation(s) were not mailed; resend each from the Team page")
        return 2
    return 0


def _read_input() -> tuple[str, Roster]:
    stdin = sys.stdin
    password = stdin.readline().rstrip("\r\n")
    if not password:
        raise AccountsStop("no password was given on the first line of stdin")
    try:
        data = json.loads(stdin.read())
    except json.JSONDecodeError as exc:
        raise AccountsStop(
            f"roster is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    try:
        return password, parse_roster(data)
    except RosterError as exc:
        raise AccountsStop(str(exc)) from None


async def _run_all(
    domain_dsn: str, identity_dsn: str, origin: str, mailer: Mailer, password: str, roster: Roster
) -> int:
    pool = db.build_pool(domain_dsn, min_size=1, max_size=4, autocommit=True)
    identity_pool = db.build_pool(identity_dsn, min_size=1, max_size=4, autocommit=True)
    await pool.open(wait=True)
    try:
        await identity_pool.open(wait=True)
        try:
            identity = IdentityService(identity_pool, IdentityConfig(origins=(origin,)))
            await identity.check_ready()
            return await run(pool, identity, mailer, password, roster, print)
        finally:
            await identity_pool.close()
    finally:
        await pool.close()


def main() -> int:
    """Run inside the gateway: 0 done, 1 refused or stopped, 2 an invitation was not mailed."""
    settings = get_settings()
    domain_dsn = str(settings.DATABASE_URL)
    identity_dsn = str(settings.IDENTITY_DATABASE_URL or "")
    if not (
        approved_target(domain_dsn, user="csa_app")
        and approved_target(identity_dsn, user="csa_identity")
    ):
        print(
            "refused: this step only runs against the staging database as csa_app and csa_identity"
        )
        return 1
    try:
        password, roster = _read_input()
        origin = settings.CONSOLE_ALLOWED_ORIGIN
        if not origin:
            raise AccountsStop("CONSOLE_ALLOWED_ORIGIN is not set in this gateway")
        return asyncio.run(
            _run_all(
                domain_dsn, identity_dsn, origin, Mailer.from_settings(settings), password, roster
            )
        )
    except AccountsStop as stop:
        print(f"stopped: {stop}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
