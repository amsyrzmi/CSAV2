"""The staging reset's company step: roster-driven companies, packages, modes and invitations."""

from __future__ import annotations

import io
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, LiteralString, cast
from uuid import UUID

import psycopg
import pytest
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.mailer import Mailer, OutgoingMail
from csa.console.packages import PACKAGES
from csa.core import db
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from scripts import staging_accounts
from scripts.staging_accounts import AccountsStop
from scripts.staging_roster import Roster, load_roster

from tests.conftest import requires_docker

EXAMPLE = Path(__file__).resolve().parent.parent / "deploy" / "staging-accounts.example.json"
IDENTITY_PASSWORD = "synthetic_accounts_identity_pw"  # NON-SECRET disposable fixture password
FIRST_PASSWORD = "synthetic-first-password-1"
ADMIN_PASSWORD = "synthetic-admin-password-2"
ORIGIN = "https://console.example.test"
SENDER = "FourStacks <no-reply@fourstacks.example.test>"
MEMBERS = 7  # five at team-jo, one each at the other two companies
FAILING = "jo.manager@example.test"  # companies[0].members[2]


class FakeTransport:
    def __init__(self, fail_for: frozenset[str] = frozenset()) -> None:
        self.fail_for = fail_for
        self.sent: list[OutgoingMail] = []

    def send(self, mail: OutgoingMail, sender: str) -> None:
        if mail.to in self.fail_for:
            raise OSError("synthetic relay failure")
        self.sent.append(mail)


@dataclass
class World:
    owner_dsn: str
    identity: IdentityService
    pool: AsyncConnectionPool
    roster: Roster
    transport: FakeTransport = field(default_factory=FakeTransport)
    admin_id: UUID | None = None

    @property
    def mailer(self) -> Mailer:
        return Mailer(self.transport, SENDER)

    async def run(
        self,
        *,
        mailer: Mailer | str | None = "default",
        password: str = ADMIN_PASSWORD,
        roster: Roster | None = None,
    ) -> tuple[int, list[str]]:
        lines: list[str] = []
        code = await staging_accounts.run(
            self.pool,
            self.identity,
            self.mailer if mailer == "default" else mailer,  # type: ignore[arg-type]
            password,
            roster or self.roster,
            lines.append,
        )
        return code, lines

    async def query(self, text: str, *params: object) -> list[dict[str, Any]]:
        async with await psycopg.AsyncConnection.connect(self.owner_dsn, autocommit=True) as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(cast(LiteralString, text), params)
                return await cursor.fetchall() if cursor.description else []

    async def count(self, table: str) -> int:
        rows = await self.query(f"SELECT count(*) AS n FROM {table}")
        return int(rows[0]["n"])


@pytest.fixture
async def world(migrated_dsn: str, app_dsn: str) -> AsyncIterator[World]:
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        async with owner.transaction():
            # Disposable fixture only: the ledgers' triggers stay off for this reset alone.
            await owner.execute("SET LOCAL session_replication_role = replica")
            await owner.execute(
                "TRUNCATE console_users, clients, console_login_attempts, console_audit CASCADE"
            )
        await owner.execute(
            sql.SQL("ALTER ROLE csa_identity LOGIN PASSWORD {}").format(
                sql.Literal(IDENTITY_PASSWORD)
            )
        )
    identity_dsn = make_conninfo(migrated_dsn, user="csa_identity", password=IDENTITY_PASSWORD)
    identity_pool = db.build_pool(identity_dsn, min_size=1, max_size=4, autocommit=True)
    pool = db.build_pool(app_dsn, min_size=1, max_size=4, autocommit=True)
    await identity_pool.open(wait=True)
    await pool.open(wait=True)
    try:
        identity = IdentityService(identity_pool, IdentityConfig(origins=(ORIGIN,)))
        roster = load_roster(EXAMPLE)
        first = roster.platform_admins[0]
        admin_id = await identity.bootstrap(first.email, FIRST_PASSWORD, first.display_name)
        actor, _, _ = await identity.login(first.email, FIRST_PASSWORD, "127.0.0.1")
        await identity.change_password(actor, FIRST_PASSWORD, ADMIN_PASSWORD)
        yield World(migrated_dsn, identity, pool, roster, admin_id=admin_id)
    finally:
        await pool.close()
        await identity_pool.close()


def _token_of(mail: OutgoingMail) -> str:
    match = re.search(r"#token=(\S+)", mail.text)
    assert match is not None
    return match.group(1)


async def _clients(world: World) -> dict[str, dict[str, Any]]:
    rows = await world.query("SELECT id, slug, name, deployment_mode, config FROM clients")
    return {row["slug"]: row for row in rows}


@requires_docker
async def test_run_creates_the_roster_and_invites_each_member(world: World) -> None:
    code, lines = await world.run()
    assert code == 0, lines
    clients = await _clients(world)
    assert set(clients) == {"team-jo", "kedai-mawar", "syarikat-cekap"}
    for company in world.roster.companies:
        client_id = clients[company.slug]["id"]
        rows = await world.query(
            "SELECT package, status FROM console_service_packages WHERE client_id=%s", client_id
        )
        assert {row["package"]: row["status"] for row in rows} == dict.fromkeys(
            company.packages, "active"
        )
        enabled = await world.query(
            "SELECT name FROM capability_registry WHERE client_id=%s AND is_enabled", client_id
        )
        wanted = {
            name
            for package in PACKAGES
            if package.key in company.packages
            for name in package.capabilities
        }
        assert {row["name"] for row in enabled} == wanted
    for slug, mode in (("team-jo", "auto"), ("kedai-mawar", "auto"), ("syarikat-cekap", "shadow")):
        assert clients[slug]["deployment_mode"] == mode
        assert clients[slug]["config"]["policy"]["deployment_mode"] == mode
    audit = await world.query(
        "SELECT client_id, actor_id FROM console_audit WHERE action = %s",
        "tenant.deployment_mode_set",
    )
    assert {row["client_id"] for row in audit} == {
        clients["team-jo"]["id"],
        clients["kedai-mawar"]["id"],
    }
    assert {row["actor_id"] for row in audit} == {world.admin_id}
    assert len(world.transport.sent) == MEMBERS
    expected = sorted(m.email for c in world.roster.companies for m in c.members)
    assert sorted(mail.to for mail in world.transport.sent) == expected
    invited = await world.query(
        "SELECT u.email, t.role, t.client_id FROM console_tokens t "
        "JOIN console_users u ON u.id = t.user_id WHERE t.kind = 'invite'"
    )
    assert {(row["email"], row["role"], row["client_id"]) for row in invited} == {
        (member.email, member.role, clients[company.slug]["id"])
        for company in world.roster.companies
        for member in company.members
    }


@requires_docker
async def test_output_has_no_token_or_link(world: World) -> None:
    code, lines = await world.run()
    assert code == 0
    assert lines
    tokens = [_token_of(mail) for mail in world.transport.sent]
    assert len(tokens) == MEMBERS
    for line in lines:
        assert "http" not in line
        assert "@" not in line
        assert not any(token in line for token in tokens)


@requires_docker
async def test_second_run_changes_nothing_and_sends_nothing(world: World) -> None:
    assert (await world.run())[0] == 0
    before = (
        await world.count("clients"),
        await world.count("console_service_packages"),
        await world.count("console_tokens"),
        await world.count("console_audit"),
    )
    sent = len(world.transport.sent)
    code, lines = await world.run()
    assert code == 0
    assert lines
    assert all("already there" in line for line in lines), lines
    assert len(world.transport.sent) == sent
    assert before == (
        await world.count("clients"),
        await world.count("console_service_packages"),
        await world.count("console_tokens"),
        await world.count("console_audit"),
    )


@requires_docker
@pytest.mark.parametrize("off", ["none", "disabled"])
async def test_refuses_with_mail_off(world: World, off: str) -> None:
    mailer = None if off == "none" else Mailer(None, SENDER)
    with pytest.raises(AccountsStop, match="mail"):
        await world.run(mailer=mailer)
    assert await world.count("clients") == 0


@requires_docker
async def test_refuses_wrong_password(world: World) -> None:
    with pytest.raises(AccountsStop) as stop:
        await world.run(password="synthetic-wrong-password")
    assert ADMIN_PASSWORD not in str(stop.value)
    assert await world.count("clients") == 0
    assert world.transport.sent == []


@requires_docker
async def test_refuses_admin_who_must_change_password(world: World) -> None:
    await world.query("UPDATE console_users SET must_change_password = true")
    with pytest.raises(AccountsStop, match="sign in and change the password first"):
        await world.run()
    assert await world.count("clients") == 0


@requires_docker
async def test_refuses_company_outside_the_roster(world: World) -> None:
    await world.query(
        "INSERT INTO clients(slug,name,platform_version,timezone) VALUES (%s,%s,'test',%s)",
        "demo-x",
        "Demo X",
        "Asia/Kuala_Lumpur",
    )
    with pytest.raises(AccountsStop, match="demo-x"):
        await world.run()
    assert await world.count("clients") == 1
    assert await world.count("console_service_packages") == 0
    assert world.transport.sent == []


@requires_docker
async def test_slug_with_a_different_name_stops(world: World) -> None:
    await world.query(
        "INSERT INTO clients(slug,name,platform_version,timezone) "
        "VALUES ('team-jo','Some Other Name','test','Asia/Kuala_Lumpur')"
    )
    with pytest.raises(AccountsStop, match="team-jo"):
        await world.run()
    assert await world.count("clients") == 1
    assert world.transport.sent == []


@requires_docker
async def test_address_in_another_company_stops(world: World) -> None:
    # An account that exists but belongs to no company is not "already there" for a member.
    taken = world.roster.companies[0].members[3]
    await world.identity.add_platform_admin(
        world.roster.platform_admins[0].email,
        ADMIN_PASSWORD,
        taken.email,
        "Someone Else",
        "synthetic-other-password-3",
    )
    with pytest.raises(AccountsStop, match=r"companies\[0\]\.members\[3\]") as stop:
        await world.run()
    assert taken.email not in str(stop.value)
    assert await world.count("clients") == 0
    assert world.transport.sent == []


@requires_docker
async def test_one_mail_failure_continues_and_reports(world: World) -> None:
    world.transport.fail_for = frozenset({FAILING})
    code, lines = await world.run()
    assert code == 2
    assert len(world.transport.sent) == MEMBERS - 1
    users = await world.query(
        "SELECT email FROM console_users WHERE NOT is_platform_admin AND email LIKE %s",
        "%@example.test",
    )
    assert len(users) == MEMBERS
    failed = [line for line in lines if "mail not sent" in line]
    assert len(failed) == 1
    assert failed[0].startswith("team-jo: members[2]")
    assert FAILING not in " ".join(lines)
    tokens = [_token_of(mail) for mail in world.transport.sent]
    assert not any(token in " ".join(lines) for token in tokens)

    world.transport.fail_for = frozenset()
    code, lines = await world.run()
    assert code == 0
    assert len(world.transport.sent) == MEMBERS - 1
    assert all("already there" in line for line in lines)
    line = next(line for line in lines if line.startswith("team-jo: members[2]"))
    assert "Team page" in line


@pytest.mark.parametrize(
    ("dsn", "user", "expected"),
    [
        ("postgresql://csa_app@staging-db/csa_staging", "csa_app", True),
        ("postgres://csa_identity@staging-db:5432/csa_staging", "csa_identity", True),
        ("postgresql://csa_app:pw@staging-db/csa_staging", "csa_app", True),
        ("postgresql://csa_app@postgres/csa", "csa_app", False),
        ("postgresql://csa@staging-db/csa_staging", "csa_app", False),
        ("postgresql://csa_app@staging-db/csa", "csa_app", False),
        ("postgresql://csa_app@staging-db:5434/csa_staging", "csa_app", False),
        ("postgresql://csa_app@staging-db/csa_staging?host=postgres", "csa_app", False),
        ("postgresql://csa_app@staging-db/csa_staging#x", "csa_app", False),
        ("mysql://csa_app@staging-db/csa_staging", "csa_app", False),
        ("postgresql://csa_app@localhost/csa_staging", "csa_app", False),
    ],
)
def test_approved_target(dsn: str, user: str, expected: bool) -> None:
    assert staging_accounts.approved_target(dsn, user=user) is expected


def test_main_refuses_a_non_staging_target_before_reading_stdin(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Forbidden(io.StringIO):
        def read(self, *args: object) -> str:
            raise AssertionError("must not read stdin")

        def readline(self, *args: object) -> str:
            raise AssertionError("must not read stdin")

        def __iter__(self) -> Forbidden:
            raise AssertionError("must not read stdin")

    settings = SimpleNamespace(
        DATABASE_URL="postgresql://csa_app:pw@postgres/csa",
        IDENTITY_DATABASE_URL="postgresql://csa_identity:pw@staging-db/csa_staging",
    )
    monkeypatch.setattr(staging_accounts, "get_settings", lambda: settings)
    monkeypatch.setattr("sys.stdin", Forbidden())
    assert staging_accounts.main() == 1
    output = capsys.readouterr()
    assert "staging" in output.out + output.err
    assert "pw" not in output.out + output.err


def test_main_stops_on_a_broken_roster_without_echoing_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        DATABASE_URL="postgresql://csa_app:pw@staging-db/csa_staging",
        IDENTITY_DATABASE_URL="postgresql://csa_identity:pw@staging-db/csa_staging",
    )
    monkeypatch.setattr(staging_accounts, "get_settings", lambda: settings)
    broken = json.dumps({"platform_admins": [], "companies": []})
    monkeypatch.setattr("sys.stdin", io.StringIO("synthetic-secret-password\n" + broken))
    assert staging_accounts.main() == 1
    output = capsys.readouterr()
    assert "roster" in output.out + output.err
    assert "synthetic-secret-password" not in output.out + output.err
