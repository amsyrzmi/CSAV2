"""Real HR SQL/API acceptance on a task-owned disposable, cached-image database.

No shared dev database, image build/pull, fake HTTP actor or provider is used.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any, LiteralString
from uuid import UUID, uuid4

import docker
import httpx
import psycopg
import pytest
from csa.console.auth import build_identity_router
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.security import digest
from csa.core import db
from csa.core.tenancy import tx
from csa.hr.api import build_hr_router
from csa.hr.models import TrainingAction, TrainingCommandInput, TrainingDraftInput
from csa.hr.policy import HrPolicyError
from csa.hr.records import HrSession
from csa.hr.service import HrService
from fastapi import FastAPI
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from tests.conftest import requires_docker, reset_console_identity

pytestmark = [requires_docker, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "https://hr.example.test"
FIELDS = ["display_name", "job_title", "department", "work_email", "assigned_manager"]
CONTENT = {"title": "Synthetic planning workshop", "learning_objective": "Practice planning"}


@pytest.mark.parametrize(
    "name", ["staff", "manager", "operator", "owner", "admin", "reader", "platform"]
)
async def test_hr_guidance_is_minimal_current_grants_not_console_role(
    world: HrWorld, name: str
) -> None:
    suffix = f"?client_id={world.tenant}" if name == "platform" else ""
    response = await world.call(name, "GET", f"/console/v1/hr/guidance{suffix}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"kind", "actions", "source", "as_of"}
    assert body["kind"] == "hr_navigation" and body["source"] == "current_hr_grants"
    expected = sorted(
        {
            row["action"]
            for row in await world.q(
                "SELECT action FROM hr_grants WHERE client_id=%s AND user_id=%s",
                (world.tenant, world.users[name]),
            )
        }
    )
    assert body["actions"] == expected
    if name in {"admin", "reader", "platform"}:
        assert body["actions"] == []
    for value in (*world.users.values(), *world.employees.values(), *world.tokens.values()):
        assert str(value) not in response.text
    assert "staff@example.test" not in response.text


async def test_hr_guidance_refresh_revokes_existing_session_and_does_not_write(
    world: HrWorld,
) -> None:
    response = await world.call("staff", "GET", "/console/v1/hr/guidance")
    assert response.status_code == 200 and "directory.read" in response.json()["actions"]
    await world.q(
        "DELETE FROM hr_grants WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["staff"]),
    )
    refreshed = await world.call("staff", "GET", "/console/v1/hr/guidance")
    assert refreshed.status_code == 200 and refreshed.json()["actions"] == []
    assert await world.q(
        "SELECT (SELECT count(*) FROM hr_training_requests) AS requests,"
        "(SELECT count(*) FROM hr_training_receipts) AS receipts,"
        "(SELECT count(*) FROM hr_training_audit) AS audit"
    ) == [{"requests": 0, "receipts": 0, "audit": 0}]
    await world.q(
        "UPDATE console_sessions SET revoked_at=now() WHERE id=%s",
        (world.sessions["staff"],),
    )
    assert (await world.call("staff", "GET", "/console/v1/hr/guidance")).status_code == 401
    with pytest.raises(HrPolicyError, match="session_unavailable"):
        await world.service.guidance(world.proof("staff"))


async def test_hr_guidance_rejects_untrusted_identity_tenant_and_query(world: HrWorld) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app), base_url=ORIGIN
    ) as anonymous:
        assert (await anonymous.get("/console/v1/hr/guidance")).status_code == 401
    assert (await world.call("platform", "GET", "/console/v1/hr/guidance")).status_code == 400
    assert (
        await world.call("staff", "GET", f"/console/v1/hr/guidance?client_id={world.other_tenant}")
    ).status_code == 403
    assert (
        await world.call(
            "platform", "GET", f"/console/v1/hr/guidance?client_id={world.other_tenant}"
        )
    ).status_code == 401
    for query in (
        "?action=training.decide",
        f"?client_id={world.tenant}&client_id={world.tenant}",
    ):
        assert (
            await world.call("staff", "GET", f"/console/v1/hr/guidance{query}")
        ).status_code == 400
    assert (
        await world.call(
            "staff", "GET", "/console/v1/hr/guidance", headers={"X-HR-Grants": "training.decide"}
        )
    ).status_code == 403
    assert (
        await world.call(
            "staff", "GET", "/console/v1/hr/guidance", headers={"Authorization": "Bearer synthetic"}
        )
    ).status_code == 401


async def test_identity_fixture_reset_clears_hr_ledgers_and_restores_protection(
    world: HrWorld,
) -> None:
    await world.submit()
    async with await psycopg.AsyncConnection.connect(world.dsn, autocommit=True) as conn:
        await reset_console_identity(conn)
        cursor = await conn.execute(
            "SELECT (SELECT count(*) FROM console_users),"
            "(SELECT count(*) FROM hr_training_requests),"
            "(SELECT count(*) FROM hr_training_receipts),"
            "(SELECT count(*) FROM hr_training_audit),"
            "current_setting('session_replication_role')"
        )
        assert await cursor.fetchone() == (0, 0, 0, 0, "origin")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_immutable_record"):
            await conn.execute("TRUNCATE hr_training_audit")


@pytest.fixture(scope="module")
def hr_database() -> Iterator[str]:
    """Use a cached image ID (no pull fallback), record ID, remove only this container."""
    client = docker.from_env()
    image = client.images.get("csa/pg16-postgis-vector:test")
    image_id = image.id
    assert image_id is not None
    task_name = f"csav2-hr-tests-{uuid4().hex}"
    container = client.containers.create(
        image_id,
        name=task_name,
        detach=True,
        environment={
            "POSTGRES_USER": "csa",
            "POSTGRES_PASSWORD": "synthetic_hr_owner",
            "POSTGRES_DB": "csa",
        },
        ports={"5432/tcp": ("127.0.0.1", 0)},
        tmpfs={"/var/lib/postgresql/data": "rw"},
        labels={"csav2.task": task_name, "csav2.fixture": "hr-disposable"},
    )
    container_id = container.id
    assert container_id is not None
    # Container ID is recorded before start so partial startup also gets cleanup.
    print(f"HR task container created: {container_id}")
    try:
        container.start()
        deadline = time.monotonic() + 45
        while True:
            # The bootstrap's Unix-socket-only server is not the final ready server.
            probe = container.exec_run(["pg_isready", "-h", "127.0.0.1", "-U", "csa", "-d", "csa"])
            if probe.exit_code == 0:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("HR disposable PostgreSQL startup timed out")
            time.sleep(0.1)
        container.reload()
        port = container.attrs["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"]
        dsn = make_conninfo(
            host="127.0.0.1", port=port, dbname="csa", user="csa", password="synthetic_hr_owner"
        )
        baseline = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                container_id,
                "psql",
                "-U",
                "csa",
                "-d",
                "csa",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input=(ROOT / "db/schema_baseline.sql").read_bytes(),
            capture_output=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            check=False,
        )
        assert baseline.returncode == 0, baseline.stderr.decode()
        migrated = subprocess.run(
            [sys.executable, "-m", "scripts.migrate"],
            cwd=ROOT,
            capture_output=True,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "NO_LOCAL_ENV": "1",
                "ENV": "ci",
                "MIGRATE_DATABASE_URL": dsn,
                "CSA_APP_PASSWORD": "synthetic_hr_app",
                "CSA_RO_PASSWORD": "synthetic_hr_ro",
                "CSA_IDENTITY_PASSWORD": "synthetic_hr_identity",
            },
            check=False,
        )
        assert migrated.returncode == 0, migrated.stderr.decode()
        yield dsn
    finally:
        container.remove(force=True, v=True)
        print(f"HR task container removed: {container_id}")
        client.close()


class HrWorld:
    def __init__(self, dsn: str, app: FastAPI, service: HrService, owner_pool: AsyncConnectionPool):
        self.dsn, self.app, self.service = dsn, app, service
        self.owner_pool = owner_pool
        self.tenant, self.other_tenant = uuid4(), uuid4()
        self.users = {
            name: uuid4()
            for name in (
                "staff",
                "manager",
                "owner",
                "operator",
                "outsider",
                "reader",
                "admin",
                "platform",
            )
        }
        self.employees = {name: uuid4() for name in ("staff", "manager", "owner", "outsider")}
        self.tokens = {name: f"synthetic-session-{uuid4()}" for name in self.users}
        self.csrf = {name: f"synthetic-csrf-{uuid4()}" for name in self.users}
        self.sessions = {name: uuid4() for name in self.users}
        self.browsers: dict[str, httpx.AsyncClient] = {}

    async def q(
        self, statement: LiteralString, params: tuple[object, ...] = ()
    ) -> list[dict[str, Any]]:
        async with self.owner_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(statement, params or None)
                return await cursor.fetchall() if cursor.description else []

    async def grant(
        self, name: str, action: str, scope: str, fields: list[str] | None = None
    ) -> None:
        await self.q(
            "INSERT INTO hr_grants(client_id,user_id,action,scope,fields) VALUES (%s,%s,%s,%s,%s)",
            (
                self.tenant,
                self.users[name],
                action,
                scope,
                fields if fields is not None else FIELDS if action == "directory.read" else [],
            ),
        )

    def proof(self, name: str) -> HrSession:
        return HrSession(
            session_id=self.sessions[name],
            subject_id=self.users[name],
            tenant_id=self.tenant,
            token_hash=SecretStr(digest(self.tokens[name])),
            csrf_hash=SecretStr(digest(self.csrf[name])),
        )

    async def call(
        self,
        name: str,
        method: str,
        path: str,
        body: Any = None,
        *,
        key: UUID | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        if name not in self.browsers:
            browser = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN
            )
            browser.cookies.set("csa_session", self.tokens[name])
            browser.cookies.set("csa_csrf", self.csrf[name])
            self.browsers[name] = browser
        supplied = {
            "Origin": ORIGIN,
            "X-CSRF-Token": self.csrf[name],
            "Idempotency-Key": str(key or uuid4()),
        }
        supplied.update(headers or {})
        return await self.browsers[name].request(method, path, json=body, headers=supplied)

    async def save(self, name: str = "staff") -> dict[str, Any]:
        response = await self.call(name, "POST", "/console/v1/hr/training-requests", CONTENT)
        assert response.status_code == 201, response.text
        return response.json()["request"]

    async def submit(self, name: str = "staff") -> dict[str, Any]:
        saved = await self.save(name)
        response = await self.call(
            name,
            "POST",
            f"/console/v1/hr/training-requests/{saved['request_id']}/transitions",
            {"action": "submit", "expected_version": 0},
        )
        assert response.status_code == 200, response.text
        return response.json()["request"]


@pytest.fixture
async def world(hr_database: str) -> AsyncIterator[HrWorld]:
    owner_pool = db.build_pool(hr_database, min_size=1, max_size=2, autocommit=True)
    await owner_pool.open(wait=True)
    domain = db.build_pool(
        make_conninfo(hr_database, user="csa_app", password="synthetic_hr_app"),
        min_size=1,
        max_size=8,
        autocommit=True,
    )
    identity = db.build_pool(
        make_conninfo(hr_database, user="csa_identity", password="synthetic_hr_identity"),
        min_size=1,
        max_size=4,
        autocommit=True,
    )
    await domain.open(wait=True)
    await identity.open(wait=True)
    service = HrService(domain, session_idle_seconds=1800)
    app = FastAPI()
    app.state.identity_service = IdentityService(identity, IdentityConfig(origins=(ORIGIN,)))
    app.state.hr_service = service
    app.include_router(build_identity_router())
    app.include_router(build_hr_router())
    w = HrWorld(hr_database, app, service, owner_pool)
    try:
        for tenant in (w.tenant, w.other_tenant):
            await w.q(
                "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,%s,'test')",
                (tenant, f"hr-{tenant}", "Synthetic HR tenant"),
            )
        for name, user in w.users.items():
            await w.q(
                "INSERT INTO console_users(id,email,display_name,is_platform_admin) "
                "VALUES (%s,%s,%s,%s)",
                (user, f"synthetic-{user}@example.test", "Synthetic Staff", name == "platform"),
            )
            role = (
                "read_only"
                if name == "reader"
                else "tenant_admin"
                if name in {"admin", "owner", "platform"}
                else "tenant_staff"
            )
            await w.q(
                "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,%s)",
                (user, w.tenant, role),
            )
            await w.q(
                "INSERT INTO console_sessions(id,user_id,token_hash,csrf_hash,client_id,"
                "expires_at) VALUES (%s,%s,%s,%s,%s,now()+interval '1 hour')",
                (
                    w.sessions[name],
                    user,
                    digest(w.tokens[name]),
                    digest(w.csrf[name]),
                    None if name == "platform" else w.tenant,
                ),
            )
        for name in ("manager", "outsider", "owner", "staff"):
            await w.q(
                "INSERT INTO hr_employees(client_id,id,display_name,job_title,department,"
                "work_email,manager_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (
                    w.tenant,
                    w.employees[name],
                    "Synthetic Staff",
                    "Trainer",
                    "Learning",
                    "staff@example.test",
                    w.employees["manager"] if name in {"staff", "owner"} else None,
                ),
            )
            await w.q(
                "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
                (w.tenant, w.users[name], w.employees[name]),
            )
        # Same local employee UUID in another tenant is a distinct employee.
        await w.q(
            "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,%s)",
            (w.other_tenant, w.employees["staff"], "Synthetic other tenant"),
        )
        await w.q(
            "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
            (w.tenant, w.users["owner"], w.users["owner"]),
        )
        for name in ("staff", "manager", "owner"):
            for action in (
                "directory.read",
                "training.read",
                "training.draft",
                "training.submit",
                "training.withdraw",
            ):
                await w.grant(name, action, "self")
        for action in ("directory.read", "training.read", "training.decide"):
            await w.grant("manager", action, "assigned_manager")
        for action in ("directory.read", "training.read"):
            await w.grant("operator", action, "hr_operator")
        await w.grant("owner", "training.decide", "client_owner")
        await service.check_ready()
        await app.state.identity_service.check_ready()
        yield w
    finally:
        for browser in w.browsers.values():
            await browser.aclose()
        await domain.close()
        await identity.close()
        await owner_pool.close()


@pytest.mark.parametrize("name", ["staff", "manager", "operator"])
async def test_real_authenticated_directory_scopes(world: HrWorld, name: str) -> None:
    response = await world.call(name, "GET", f"/console/v1/hr/employees/{world.employees['staff']}")
    assert response.status_code == 200, response.text
    assert set(response.json()["values"]) == set(FIELDS)
    assert response.json()["employee"]["tenant_id"] == str(world.tenant)
    assert "console_account_id" not in response.text
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("name", ["outsider", "admin", "reader", "platform"])
async def test_broad_role_or_unrelated_employee_gives_no_hr_access(
    world: HrWorld, name: str
) -> None:
    suffix = f"?client_id={world.tenant}" if name == "platform" else ""
    response = await world.call(
        name, "GET", f"/console/v1/hr/employees/{world.employees['staff']}{suffix}"
    )
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "resource_unavailable"}


async def test_anonymous_bearer_and_cross_tenant_query_denied(world: HrWorld) -> None:
    path = f"/console/v1/hr/employees/{world.employees['staff']}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app), base_url=ORIGIN
    ) as client:
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(path, headers={"Authorization": "Bearer synthetic"})
        ).status_code == 401
    assert (
        await world.call("staff", "GET", f"{path}?client_id={world.other_tenant}")
    ).status_code == 403
    assert (await world.call("platform", "GET", path)).status_code == 400


@pytest.mark.parametrize(
    "header",
    ["X-Actor-Id", "X-Role", "X-Client-Id", "X-Employee-Id", "X-HR-Grants", "X-Client-Owner"],
)
async def test_http_authority_headers_rejected(world: HrWorld, header: str) -> None:
    response = await world.call(
        "staff",
        "GET",
        f"/console/v1/hr/employees/{world.employees['staff']}",
        headers={header: "untrusted"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "extra", ["actor", "grants", "tenant_id", "employee", "client_owner", "assigned_manager"]
)
async def test_http_draft_authority_injection_rejected_without_echo(
    world: HrWorld, extra: str
) -> None:
    response = await world.call(
        "staff",
        "POST",
        "/console/v1/hr/training-requests",
        {**CONTENT, extra: "untrusted-private-value"},
    )
    assert response.status_code == 422
    assert "untrusted-private-value" not in response.text
    assert CONTENT["title"] not in response.text
    assert response.headers["cache-control"] == "no-store"


async def test_query_authority_and_duplicate_tenant_rejected(world: HrWorld) -> None:
    path = f"/console/v1/hr/employees/{world.employees['staff']}"
    for query in ("grants=hr_operator", f"client_id={world.tenant}&client_id={world.tenant}"):
        assert (await world.call("staff", "GET", f"{path}?{query}")).status_code == 400


@pytest.mark.parametrize(
    "headers", [{"Origin": "https://other.example.test"}, {"X-CSRF-Token": "wrong"}]
)
async def test_csrf_origin_denied(world: HrWorld, headers: dict[str, str]) -> None:
    response = await world.call(
        "staff", "POST", "/console/v1/hr/training-requests", CONTENT, headers=headers
    )
    assert response.status_code == 403


async def test_body_and_field_bounds_and_missing_key(world: HrWorld) -> None:
    path = "/console/v1/hr/training-requests"
    assert (
        await world.call("staff", "POST", path, {**CONTENT, "title": "x" * 161})
    ).status_code == 422
    assert (
        await world.call("staff", "POST", path, {**CONTENT, "title": "x" * 9000})
    ).status_code == 413
    assert (
        await world.call("staff", "POST", path, CONTENT, headers={"Idempotency-Key": "not-a-uuid"})
    ).status_code == 422


async def test_persisted_draft_submit_decide_read_and_audit(world: HrWorld) -> None:
    submitted = await world.submit()
    response = await world.call(
        "manager",
        "POST",
        f"/console/v1/hr/training-requests/{submitted['request_id']}/transitions",
        {"action": "approve", "expected_version": 1, "decision_note": "Synthetic private note"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "persisted_training"
    assert response.json()["request"]["status"] == "approved"
    assert response.json()["request"]["version"] == 2
    assert "Synthetic private note" not in response.text and "decision_by" not in response.text
    for name in ("staff", "manager", "operator"):
        view = await world.call(
            name, "GET", f"/console/v1/hr/training-requests/{submitted['request_id']}"
        )
        assert view.status_code == 200 and view.json()["status"] == "approved"
        assert "Synthetic private note" not in view.text
    rows = await world.q(
        "SELECT action,before_status,after_status,version FROM hr_training_audit "
        "WHERE client_id=%s ORDER BY version",
        (world.tenant,),
    )
    assert [r["action"] for r in rows] == ["save_draft", "submit", "approve"]
    assert [r["version"] for r in rows] == [0, 1, 2]
    assert (
        await world.q(
            "SELECT count(*) AS n FROM hr_training_receipts WHERE client_id=%s", (world.tenant,)
        )
    )[0]["n"] == 3


@pytest.mark.parametrize("name", ["staff", "operator", "outsider"])
async def test_ungranted_or_outside_manager_cannot_decide(world: HrWorld, name: str) -> None:
    submitted = await world.submit()
    response = await world.call(
        name,
        "POST",
        f"/console/v1/hr/training-requests/{submitted['request_id']}/transitions",
        {"action": "approve", "expected_version": 1},
    )
    assert response.status_code == 404


async def test_owner_can_approve_own_and_no_manager_blocks(world: HrWorld) -> None:
    submitted = await world.submit("owner")
    path = f"/console/v1/hr/training-requests/{submitted['request_id']}/transitions"
    await world.q(
        "UPDATE hr_employees SET manager_id=NULL WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["owner"]),
    )
    assert (
        await world.call("owner", "POST", path, {"action": "approve", "expected_version": 1})
    ).status_code == 404
    await world.q(
        "UPDATE hr_employees SET manager_id=%s WHERE client_id=%s AND id=%s",
        (world.employees["owner"], world.tenant, world.employees["owner"]),
    )
    response = await world.call("owner", "POST", path, {"action": "approve", "expected_version": 1})
    assert response.status_code == 200, response.text


async def test_ordinary_manager_cannot_approve_own(world: HrWorld) -> None:
    await world.q(
        "UPDATE hr_employees SET manager_id=id WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["manager"]),
    )
    submitted = await world.submit("manager")
    response = await world.call(
        "manager",
        "POST",
        f"/console/v1/hr/training-requests/{submitted['request_id']}/transitions",
        {"action": "approve", "expected_version": 1},
    )
    assert response.status_code == 404


async def test_replay_returns_original_receipt_after_later_transition(world: HrWorld) -> None:
    key = uuid4()
    first = await world.call("staff", "POST", "/console/v1/hr/training-requests", CONTENT, key=key)
    request_id = first.json()["request"]["request_id"]
    assert (
        await world.call(
            "staff",
            "POST",
            f"/console/v1/hr/training-requests/{request_id}/transitions",
            {"action": "submit", "expected_version": 0},
        )
    ).status_code == 200
    again = await world.call("staff", "POST", "/console/v1/hr/training-requests", CONTENT, key=key)
    assert again.status_code == 201 and again.json()["duplicate"] is True
    assert again.json()["request"] == first.json()["request"]
    conflict = await world.call(
        "staff",
        "POST",
        "/console/v1/hr/training-requests",
        {**CONTENT, "title": "Changed workshop"},
        key=key,
    )
    assert conflict.status_code == 409 and conflict.json()["detail"] == "idempotency_conflict"
    await world.q(
        "DELETE FROM hr_grants WHERE client_id=%s AND user_id=%s AND action='training.draft'",
        (world.tenant, world.users["staff"]),
    )
    assert (
        await world.call("staff", "POST", "/console/v1/hr/training-requests", CONTENT, key=key)
    ).status_code == 404


async def test_stale_terminal_and_rejection_reason(world: HrWorld) -> None:
    submitted = await world.submit()
    path = f"/console/v1/hr/training-requests/{submitted['request_id']}/transitions"
    stale = await world.call("manager", "POST", path, {"action": "approve", "expected_version": 0})
    assert stale.status_code == 409 and stale.json()["detail"] == "stale_version"
    assert (
        await world.call("manager", "POST", path, {"action": "reject", "expected_version": 1})
    ).status_code == 422
    rejected = await world.call(
        "manager",
        "POST",
        path,
        {"action": "reject", "expected_version": 1, "decision_note": "Synthetic reason"},
    )
    assert rejected.status_code == 200
    terminal = await world.call(
        "staff", "POST", path, {"action": "withdraw", "expected_version": 2}
    )
    assert terminal.status_code == 409 and terminal.json()["detail"] == "invalid_transition"


async def test_withdraw_draft(world: HrWorld) -> None:
    saved = await world.save()
    response = await world.call(
        "staff",
        "POST",
        f"/console/v1/hr/training-requests/{saved['request_id']}/transitions",
        {"action": "withdraw", "expected_version": 0},
    )
    assert response.status_code == 200 and response.json()["request"]["status"] == "withdrawn"


async def test_employee_without_account_and_owner_without_employee_link(world: HrWorld) -> None:
    employee = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name,manager_id) VALUES (%s,%s,%s,%s)",
        (world.tenant, employee, "Synthetic unlinked employee", world.employees["manager"]),
    )
    response = await world.call("operator", "GET", f"/console/v1/hr/employees/{employee}")
    assert response.status_code == 200 and "console_account_id" not in response.text
    submitted = await world.submit()
    await world.q(
        "DELETE FROM hr_employee_accounts WHERE client_id=%s AND user_id=%s",
        (world.tenant, world.users["owner"]),
    )
    decision = await world.call(
        "owner",
        "POST",
        f"/console/v1/hr/training-requests/{submitted['request_id']}/transitions",
        {"action": "approve", "expected_version": 1},
    )
    assert decision.status_code == 200, decision.text
    assert (
        await world.call("owner", "POST", "/console/v1/hr/training-requests", CONTENT)
    ).status_code == 404


async def test_database_field_filtering_and_unsupported_grants(world: HrWorld) -> None:
    await world.q(
        "UPDATE hr_grants SET fields=ARRAY['display_name'] WHERE client_id=%s "
        "AND user_id=%s AND action='directory.read'",
        (world.tenant, world.users["staff"]),
    )
    response = await world.call(
        "staff", "GET", f"/console/v1/hr/employees/{world.employees['staff']}"
    )
    assert response.status_code == 200 and set(response.json()["values"]) == {"display_name"}
    with pytest.raises(psycopg.errors.CheckViolation):
        await world.grant("operator", "training.decide", "hr_operator")
    for forbidden_field in ("salary", "bank", "medical", "cv"):
        with pytest.raises(psycopg.errors.CheckViolation):
            await world.q(
                "UPDATE hr_grants SET fields=%s WHERE client_id=%s AND user_id=%s "
                "AND action='directory.read'",
                ([forbidden_field], world.tenant, world.users["staff"]),
            )


async def test_tenant_composite_foreign_keys(world: HrWorld) -> None:
    foreign_manager = uuid4()
    await world.q(
        "INSERT INTO hr_employees(client_id,id,display_name) VALUES (%s,%s,%s)",
        (world.other_tenant, foreign_manager, "Synthetic foreign manager"),
    )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await world.q(
            "UPDATE hr_employees SET manager_id=%s WHERE client_id=%s AND id=%s",
            (foreign_manager, world.tenant, world.employees["staff"]),
        )
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await world.q(
            "INSERT INTO hr_employee_accounts(client_id,user_id,employee_id) VALUES (%s,%s,%s)",
            (world.other_tenant, world.users["staff"], world.employees["staff"]),
        )


async def test_rls_and_role_privileges_fail_closed(world: HrWorld) -> None:
    async with world.service.pool.connection() as conn:
        assert not await (await conn.execute("SELECT * FROM hr_employees")).fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("SELECT * FROM console_sessions")
        for statement in (
            "UPDATE hr_grants SET scope='hr_operator'",
            "UPDATE hr_client_owners SET user_id=user_id",
            "UPDATE hr_employee_accounts SET employee_id=employee_id",
            "UPDATE hr_employees SET display_name='Changed'",
            "DELETE FROM hr_training_audit",
            "DELETE FROM hr_training_receipts",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(statement)  # type: ignore[arg-type]
    async with tx(world.service.pool, str(world.other_tenant)) as conn:
        assert not await (await conn.execute("SELECT * FROM hr_employees")).fetchall()
    for role, password in (
        ("csa_ro", "synthetic_hr_ro"),
        ("csa_identity", "synthetic_hr_identity"),
    ):
        async with await psycopg.AsyncConnection.connect(
            make_conninfo(world.dsn, user=role, password=password), autocommit=True
        ) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT * FROM hr_employees")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("SELECT hr_lock_session(NULL,NULL,NULL,NULL,1800,NULL)")
    staged = await world.q(
        "SELECT relrowsecurity,relforcerowsecurity FROM pg_class "
        "WHERE relname LIKE 'hr_%' AND relkind='r'"
    )
    assert len(staged) == 27 and all(
        r["relrowsecurity"] and not r["relforcerowsecurity"] for r in staged
    )


async def test_read_only_membership_cannot_mutate_even_with_explicit_hr_grant(
    world: HrWorld,
) -> None:
    response = await world.call("reader", "POST", "/console/v1/hr/training-requests", CONTENT)
    assert response.status_code == 403 and response.json()["detail"] == "read_only_session"
    with pytest.raises(HrPolicyError, match="session_unavailable"):
        await world.service.save_draft(
            world.proof("reader"), TrainingDraftInput(**CONTENT), uuid4()
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "session",
        "user",
        "password",
        "membership",
        "owner",
        "grant",
        "manager",
        "inactive_manager",
        "link",
    ],
)
async def test_current_authority_is_rechecked_for_each_decision(
    world: HrWorld, mutation: str
) -> None:
    submitted = await world.submit()
    name = "owner" if mutation == "owner" else "manager"
    if mutation == "session":
        await world.q(
            "UPDATE console_sessions SET revoked_at=now() WHERE id=%s", (world.sessions[name],)
        )
    elif mutation == "user":
        await world.q("UPDATE console_users SET is_enabled=false WHERE id=%s", (world.users[name],))
    elif mutation == "password":
        await world.q(
            "UPDATE console_users SET must_change_password=true WHERE id=%s", (world.users[name],)
        )
    elif mutation == "membership":
        await world.q(
            "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s AND client_id=%s",
            (world.users[name], world.tenant),
        )
    elif mutation == "owner":
        await world.q("DELETE FROM hr_client_owners WHERE client_id=%s", (world.tenant,))
    elif mutation == "grant":
        await world.q(
            "DELETE FROM hr_grants WHERE client_id=%s AND user_id=%s AND action='training.decide'",
            (world.tenant, world.users[name]),
        )
    elif mutation == "manager":
        await world.q(
            "UPDATE hr_employees SET manager_id=%s WHERE client_id=%s AND id=%s",
            (world.employees["outsider"], world.tenant, world.employees["staff"]),
        )
    elif mutation == "inactive_manager":
        await world.q(
            "UPDATE hr_employees SET is_active=false WHERE client_id=%s AND id=%s",
            (world.tenant, world.employees["manager"]),
        )
    else:
        await world.q(
            "DELETE FROM hr_employee_accounts WHERE client_id=%s AND user_id=%s",
            (world.tenant, world.users[name]),
        )
    command = TrainingCommandInput(action=TrainingAction.APPROVE, expected_version=1)
    with pytest.raises(HrPolicyError):
        await world.service.transition(
            world.proof(name), UUID(submitted["request_id"]), command, uuid4()
        )
    row = (
        await world.q(
            "SELECT status,version FROM hr_training_requests WHERE client_id=%s AND id=%s",
            (world.tenant, UUID(submitted["request_id"])),
        )
    )[0]
    assert row == {"status": "submitted", "version": 1}


async def test_tampered_session_proof_and_missing_csrf_fail_in_service(world: HrWorld) -> None:
    proof = world.proof("staff")
    no_csrf = proof.model_copy(update={"csrf_hash": None})
    with pytest.raises(HrPolicyError, match="csrf_required"):
        await world.service.save_draft(no_csrf, TrainingDraftInput(**CONTENT), uuid4())
    bad_token = proof.model_copy(
        update={"token_hash": SecretStr(digest("incorrect-synthetic-token"))}
    )
    with pytest.raises(HrPolicyError, match="session_unavailable"):
        await world.service.save_draft(bad_token, TrainingDraftInput(**CONTENT), uuid4())


async def test_concurrent_same_key_has_one_transition_and_receipt(world: HrWorld) -> None:
    saved = await world.save()
    key = uuid4()
    command = TrainingCommandInput(action=TrainingAction.SUBMIT, expected_version=0)
    first, second = await asyncio.gather(
        *[
            world.service.transition(world.proof("staff"), UUID(saved["request_id"]), command, key)
            for _ in range(2)
        ]
    )
    assert {first.duplicate, second.duplicate} == {False, True}
    assert first.request == second.request
    assert (
        await world.q(
            "SELECT count(*) AS n FROM hr_training_receipts WHERE client_id=%s", (world.tenant,)
        )
    )[0]["n"] == 2
    assert (
        await world.q(
            "SELECT count(*) AS n FROM hr_training_audit WHERE client_id=%s", (world.tenant,)
        )
    )[0]["n"] == 2


async def test_competing_decisions_commit_one_winner(world: HrWorld) -> None:
    submitted = await world.submit()
    results = await asyncio.gather(
        world.service.transition(
            world.proof("manager"),
            UUID(submitted["request_id"]),
            TrainingCommandInput(action=TrainingAction.APPROVE, expected_version=1),
            uuid4(),
        ),
        world.service.transition(
            world.proof("owner"),
            UUID(submitted["request_id"]),
            TrainingCommandInput(
                action=TrainingAction.REJECT,
                expected_version=1,
                decision_note="Synthetic competing decision",
            ),
            uuid4(),
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, HrPolicyError) for result in results) == 1
    failed = next(result for result in results if isinstance(result, HrPolicyError))
    assert failed.code == "stale_version"
    row = (
        await world.q(
            "SELECT status,version FROM hr_training_requests WHERE client_id=%s AND id=%s",
            (world.tenant, UUID(submitted["request_id"])),
        )
    )[0]
    assert row["status"] in {"approved", "rejected"} and row["version"] == 2
    assert (
        await world.q(
            "SELECT count(*) AS n FROM hr_training_audit WHERE client_id=%s", (world.tenant,)
        )
    )[0]["n"] == 3


async def test_audit_failure_rolls_back_state_and_receipt(world: HrWorld) -> None:
    saved = await world.save()
    await world.q(
        "ALTER TABLE hr_training_audit ADD CONSTRAINT hr_test_audit_failure "
        "CHECK (action <> 'submit') NOT VALID"
    )
    key = uuid4()
    path = f"/console/v1/hr/training-requests/{saved['request_id']}/transitions"
    try:
        response = await world.call(
            "staff", "POST", path, {"action": "submit", "expected_version": 0}, key=key
        )
        assert (
            response.status_code == 503 and response.json()["detail"] == "hr_database_unavailable"
        )
        row = (
            await world.q(
                "SELECT status,version FROM hr_training_requests WHERE client_id=%s AND id=%s",
                (world.tenant, UUID(saved["request_id"])),
            )
        )[0]
        assert row == {"status": "draft", "version": 0}
        assert not await world.q(
            "SELECT * FROM hr_training_receipts WHERE client_id=%s AND request_key=%s",
            (world.tenant, key),
        )
        assert (
            await world.q(
                "SELECT count(*) AS n FROM hr_training_audit WHERE client_id=%s", (world.tenant,)
            )
        )[0]["n"] == 1
    finally:
        await world.q("ALTER TABLE hr_training_audit DROP CONSTRAINT hr_test_audit_failure")
    retry = await world.call(
        "staff", "POST", path, {"action": "submit", "expected_version": 0}, key=key
    )
    assert retry.status_code == 200 and retry.json()["duplicate"] is False


async def test_database_requires_coupled_audit_and_immutable_receipts(world: HrWorld) -> None:
    saved = await world.save()
    proof = world.proof("staff")
    with pytest.raises(psycopg.errors.CheckViolation, match="hr_audit_required"):
        async with tx(world.service.pool, str(world.tenant)) as conn:
            await world.service._access(conn, proof)
            await conn.execute(
                "UPDATE hr_training_requests SET status='submitted',version=1,"
                "updated_at=clock_timestamp() WHERE client_id=%s AND id=%s",
                (world.tenant, UUID(saved["request_id"])),
            )
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_immutable_record"):
        await world.q(
            "UPDATE hr_training_audit SET action=action WHERE client_id=%s", (world.tenant,)
        )
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="hr_immutable_record"):
        await world.q("DELETE FROM hr_training_receipts WHERE client_id=%s", (world.tenant,))


async def test_api_contract_exposes_only_bounded_hr_operations(world: HrWorld) -> None:
    schema = world.app.openapi()
    hr_paths = {path for path in schema["paths"] if path.startswith("/console/v1/hr")}
    assert hr_paths == {
        "/console/v1/hr/context",
        "/console/v1/hr/guidance",
        "/console/v1/hr/operations",
        "/console/v1/hr/assistant",
        "/console/v1/hr/records/{area}",
        "/console/v1/hr/private/{area}/{resource_id}",
        "/console/v1/hr/employees",
        "/console/v1/hr/agents",
        "/console/v1/hr/agents/contracts",
        "/console/v1/hr/agents/execute",
        "/console/v1/hr/confirmations/{confirmation_id}/confirm",
        "/console/v1/hr/workflows",
        "/console/v1/hr/workflows/{case_id}",
        "/console/v1/hr/workflows/{case_id}/transitions",
        "/console/v1/hr/employees/{employee_id}",
        "/console/v1/hr/training-requests",
        "/console/v1/hr/training-requests/{request_id}",
        "/console/v1/hr/training-requests/{request_id}/transitions",
    }
    assert schema["components"]["schemas"]["TrainingDraftInput"]["additionalProperties"] is False


async def test_readiness_rejects_owner_pool_and_disabled_hr_rls(world: HrWorld) -> None:
    with pytest.raises(RuntimeError, match="HR requires"):
        await HrService(world.owner_pool, session_idle_seconds=1800).check_ready()
    await world.q("ALTER TABLE hr_grants DISABLE ROW LEVEL SECURITY")
    try:
        with pytest.raises(RuntimeError, match="HR requires"):
            await world.service.check_ready()
    finally:
        await world.q("ALTER TABLE hr_grants ENABLE ROW LEVEL SECURITY")
    await world.service.check_ready()


async def switch(world: HrWorld, name: str, enabled: bool = True) -> None:
    response = await world.call(
        "owner", "PUT", "/console/v1/hr/agents", {"name": name, "enabled": enabled}
    )
    assert response.status_code == 200, response.text


async def workflow_grant(world: HrWorld, who: str, domain: str, action: str, scope: str) -> None:
    await world.q(
        "INSERT INTO hr_workflow_grants(client_id,user_id,domain,action,scope) "
        "VALUES(%s,%s,%s,%s,%s)",
        (world.tenant, world.users[who], domain, action, scope),
    )


async def test_new_tenant_staff_agents_disabled_and_only_explicit_owner_can_enable(
    world: HrWorld,
) -> None:
    response = await world.call("admin", "GET", "/console/v1/hr/agents")
    assert response.status_code == 200
    assert len(response.json()) == 6
    assert not any(setting["enabled"] for setting in response.json())
    for name in ("admin", "staff", "manager"):
        denied = await world.call(
            name, "PUT", "/console/v1/hr/agents", {"name": "hr_training", "enabled": True}
        )
        assert denied.status_code == 404, denied.text
    body = {"action": "training.prepare", "content": CONTENT}
    assert (await world.call("staff", "POST", "/console/v1/hr/agents/execute", body)).json()[
        "detail"
    ] == "agent_disabled"
    await switch(world, "hr_training")
    key = uuid4()
    responses = await asyncio.gather(
        *[
            world.call("staff", "POST", "/console/v1/hr/agents/execute", body, key=key)
            for _ in range(2)
        ]
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    assert responses[0].json()["request"] == responses[1].json()["request"]
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_training_requests WHERE client_id=%s", (world.tenant,)
    )
    assert rows[0]["n"] == 1
    await switch(world, "hr_training", False)
    assert (
        await world.call("staff", "POST", "/console/v1/hr/agents/execute", body, key=key)
    ).json()["detail"] == "agent_disabled"


async def test_agent_decision_waits_for_bound_human_confirmation_and_double_tap_is_one_event(
    world: HrWorld,
) -> None:
    submitted = await world.submit()
    await switch(world, "hr_training")
    body = {
        "action": "training.transition",
        "request_id": submitted["request_id"],
        "command": {"action": "approve", "expected_version": 1, "decision_note": "Reviewed"},
    }
    prepared = await world.call("manager", "POST", "/console/v1/hr/agents/execute", body)
    assert prepared.status_code == 200, prepared.text
    assert prepared.json()["kind"] == "confirmation_required"
    row = await world.q(
        "SELECT status FROM hr_training_requests WHERE client_id=%s AND id=%s",
        (world.tenant, UUID(submitted["request_id"])),
    )
    assert row[0]["status"] == "submitted"
    path = f"/console/v1/hr/confirmations/{prepared.json()['confirmation_id']}/confirm"
    assert (await world.call("owner", "POST", path, {})).status_code == 404
    assert (
        await world.call("manager", "POST", path, {"command": {"action": "reject"}})
    ).status_code == 422
    results = await asyncio.gather(*[world.call("manager", "POST", path, {}) for _ in range(2)])
    assert all(r.status_code == 200 for r in results), [r.text for r in results]
    assert all(r.json()["request"]["status"] == "approved" for r in results)
    events = await world.q(
        "SELECT count(*) AS n FROM hr_training_audit WHERE client_id=%s AND request_id=%s",
        (world.tenant, UUID(submitted["request_id"])),
    )
    assert events[0]["n"] == 3


@pytest.mark.parametrize("revoked", ["grant", "switch", "expired", "version"])
async def test_confirmation_rechecks_current_permission_expiry_and_version(
    world: HrWorld, revoked: str
) -> None:
    submitted = await world.submit()
    await switch(world, "hr_training")
    prepared = await world.call(
        "manager",
        "POST",
        "/console/v1/hr/agents/execute",
        {
            "action": "training.transition",
            "request_id": submitted["request_id"],
            "command": {"action": "approve", "expected_version": 1},
        },
    )
    assert prepared.status_code == 200, prepared.text
    if revoked == "grant":
        await world.q(
            "DELETE FROM hr_grants WHERE client_id=%s AND user_id=%s AND action='training.decide'",
            (world.tenant, world.users["manager"]),
        )
    elif revoked == "switch":
        await switch(world, "hr_training", False)
    elif revoked == "expired":
        from datetime import timedelta

        previous = world.service.now
        world.service.now = lambda: previous() + timedelta(minutes=16)
    else:
        response = await world.call(
            "staff",
            "POST",
            f"/console/v1/hr/training-requests/{submitted['request_id']}/transitions",
            {"action": "withdraw", "expected_version": 1},
        )
        assert response.status_code == 200
    response = await world.call(
        "manager",
        "POST",
        f"/console/v1/hr/confirmations/{prepared.json()['confirmation_id']}/confirm",
        {},
    )
    assert response.status_code in {404, 409}, response.text
    rows = await world.q(
        "SELECT status FROM hr_training_requests WHERE client_id=%s AND id=%s",
        (world.tenant, UUID(submitted["request_id"])),
    )
    assert rows[0]["status"] == ("withdrawn" if revoked == "version" else "submitted")


async def test_directory_and_training_pages_are_scoped_and_cursor_bounded(world: HrWorld) -> None:
    await world.save()
    directory = await world.call("staff", "GET", "/console/v1/hr/employees?limit=1")
    assert directory.status_code == 200, directory.text
    assert [r["employee"]["employee_id"] for r in directory.json()["items"]] == [
        str(world.employees["staff"])
    ]
    assert directory.json().get("next_cursor") is None
    assert (await world.call("admin", "GET", "/console/v1/hr/employees")).status_code == 404
    assert (
        await world.call("staff", "GET", "/console/v1/hr/employees?limit=51")
    ).status_code == 422
    training = await world.call("manager", "GET", "/console/v1/hr/training-requests")
    assert training.status_code == 200 and len(training.json()["items"]) == 1


ATTENDANCE = {
    "domain": "attendance",
    "region": "sabah",
    "timezone": "Asia/Kuching",
    "starts_at": "2026-10-01T09:00:00+08:00",
    "ends_at": "2026-10-01T18:00:00+08:00",
    "reason": "Synthetic attendance",
}


async def test_attendance_agent_prepares_only_and_manager_decision_is_manual(
    world: HrWorld,
) -> None:
    await workflow_grant(world, "staff", "attendance", "prepare", "self")
    await workflow_grant(world, "staff", "attendance", "read", "self")
    await workflow_grant(world, "manager", "attendance", "decide", "assigned_manager")
    await switch(world, "hr_attendance")
    key = uuid4()
    body = {"action": "workflow.prepare", "content": ATTENDANCE}
    responses = await asyncio.gather(
        *[
            world.call("staff", "POST", "/console/v1/hr/agents/execute", body, key=key)
            for _ in range(2)
        ]
    )
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    saved = responses[0].json()
    assert saved == responses[1].json()
    assert saved["status"] == "pending" and saved["execution"] == "local_workflow_only"
    assert saved["employee"]["employee_id"] == str(world.employees["staff"])
    path = f"/console/v1/hr/workflows/{saved['id']}/transitions"
    command = {"action": "approve", "expected_version": 0}
    assert (await world.call("staff", "POST", path, command)).status_code == 404
    page = await world.call("manager", "GET", "/console/v1/hr/workflows")
    assert page.status_code == 200 and len(page.json()["items"]) == 1
    result = await world.call("manager", "POST", path, command)
    assert result.status_code == 200 and result.json()["status"] == "approved", result.text
    assert (
        await world.call("outsider", "GET", f"/console/v1/hr/workflows/{saved['id']}")
    ).status_code == 404
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_case_events WHERE client_id=%s", (world.tenant,)
    )
    assert rows[0]["n"] == 2


async def test_attendance_overlap_and_second_correction_are_blocked(world: HrWorld) -> None:
    await workflow_grant(world, "staff", "attendance", "prepare", "self")
    await workflow_grant(world, "manager", "attendance", "decide", "assigned_manager")

    async def prepare(content: dict) -> dict:
        result = await world.call("staff", "POST", "/console/v1/hr/workflows", content)
        assert result.status_code == 201, result.text
        return result.json()

    async def approve(case: dict) -> httpx.Response:
        return await world.call(
            "manager",
            "POST",
            f"/console/v1/hr/workflows/{case['id']}/transitions",
            {"action": "approve", "expected_version": 0},
        )

    original = await prepare(ATTENDANCE)
    assert (await approve(original)).status_code == 200
    overlapping = await prepare(ATTENDANCE)
    assert (await approve(overlapping)).json()["detail"] == "attendance_overlap"
    correction = await prepare(
        {**ATTENDANCE, "correction_of": original["id"], "ends_at": "2026-10-01T17:00:00+08:00"}
    )
    assert (await approve(correction)).status_code == 200
    second = await prepare(
        {
            **ATTENDANCE,
            "correction_of": original["id"],
            "starts_at": "2026-10-02T09:00:00+08:00",
            "ends_at": "2026-10-02T18:00:00+08:00",
        }
    )
    assert (await approve(second)).json()["detail"] == "attendance_already_corrected"


@pytest.mark.parametrize("region", ["peninsular_labuan", "sabah", "sarawak"])
async def test_leave_requires_active_assigned_manager_for_all_regions(
    world: HrWorld, region: str
) -> None:
    await workflow_grant(world, "staff", "leave", "prepare", "self")
    await workflow_grant(world, "owner", "leave", "decide", "client_owner")
    response = await world.call(
        "staff",
        "POST",
        "/console/v1/hr/workflows",
        {
            "domain": "leave",
            "region": region,
            "starts_on": "2026-11-01",
            "ends_on": "2026-11-03",
            "leave_type": "annual",
            "reason": "Synthetic",
        },
    )
    assert response.status_code == 201, response.text
    await world.q(
        "UPDATE hr_employees SET manager_id=NULL WHERE client_id=%s AND id=%s",
        (world.tenant, world.employees["staff"]),
    )
    denied = await world.call(
        "owner",
        "POST",
        f"/console/v1/hr/workflows/{response.json()['id']}/transitions",
        {"action": "approve", "expected_version": 0},
    )
    assert denied.status_code == 404, denied.text


async def test_recruitment_guide_is_automatic_but_candidate_intake_requires_verified_consent(
    world: HrWorld,
) -> None:
    await workflow_grant(world, "operator", "recruitment", "prepare", "hr_operator")
    await switch(world, "hr_recruitment")
    content = {
        "domain": "recruitment",
        "region": "sarawak",
        "role_title": "Synthetic Engineer",
        "competencies": ["Testing"],
    }
    response = await world.call(
        "operator",
        "POST",
        "/console/v1/hr/agents/execute",
        {"action": "workflow.prepare", "content": content},
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["interview_guide"]) == 1
    assert response.json()["status"] == "pending"
    denied = await world.call(
        "operator",
        "POST",
        "/console/v1/hr/workflows",
        {**content, "candidate_reference": str(uuid4()), "consent_reference": str(uuid4())},
    )
    assert denied.json()["detail"] == "verified_candidate_intake_required"


async def test_payroll_requires_financial_grants_distinct_reviewer_and_manual_action(
    world: HrWorld,
) -> None:
    content = {
        "domain": "payroll",
        "region": "sabah",
        "legal_entity_reference": "Synthetic Entity",
        "starts_on": "2026-10-01",
        "ends_on": "2026-10-31",
        "cycle": "monthly",
        "rule_set_reference": str(uuid4()),
    }
    assert (
        await world.call("owner", "POST", "/console/v1/hr/workflows", content)
    ).status_code == 404
    await workflow_grant(world, "operator", "payroll", "prepare", "payroll_operator")
    await workflow_grant(world, "operator", "payroll", "decide", "payroll_reviewer")
    await workflow_grant(world, "owner", "payroll", "decide", "payroll_reviewer")
    await switch(world, "hr_payroll")
    denied = await world.call(
        "operator",
        "POST",
        "/console/v1/hr/agents/execute",
        {"action": "workflow.prepare", "content": content},
    )
    assert denied.json()["detail"] == "manual_payroll_preparation_required"
    response = await world.call("operator", "POST", "/console/v1/hr/workflows", content)
    assert response.status_code == 201, response.text
    path = f"/console/v1/hr/workflows/{response.json()['id']}/transitions"
    command = {"action": "approve", "expected_version": 0}
    assert (await world.call("operator", "POST", path, command)).json()[
        "detail"
    ] == "distinct_payroll_reviewer_required"
    approved = await world.call("owner", "POST", path, command)
    # Financial reviewer grants alone no longer authorize private payroll approval.
    assert approved.status_code == 404, approved.text
    rows = await world.q(
        "SELECT status FROM hr_cases WHERE client_id=%s AND id=%s",
        (world.tenant, UUID(response.json()["id"])),
    )
    assert rows[0]["status"] == "pending"


async def test_observe_loop_never_saves_a_draft(world: HrWorld) -> None:
    from csa.agents.hr import HR_AGENT_INPUT, HrAgentDispatcher

    inp = HR_AGENT_INPUT.validate_python({"action": "training.prepare", "content": CONTENT})
    with pytest.raises(HrPolicyError, match="observe_loop_read_only"):
        await HrAgentDispatcher(world.service).execute(
            world.proof("staff"), inp, uuid4(), observe_only=True
        )
    rows = await world.q(
        "SELECT count(*) AS n FROM hr_training_requests WHERE client_id=%s", (world.tenant,)
    )
    assert rows[0]["n"] == 0


async def test_agent_contract_catalog_matches_current_switches_and_forbids_extra_inputs(
    world: HrWorld,
) -> None:
    response = await world.call("staff", "GET", "/console/v1/hr/agents/contracts")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 6
    for contract in response.json():
        assert contract["timeout_ms"] == 10000
        assert contract["max_retries"] == 0
        assert contract["enabled"] is False
        if contract["name"] == "hr_assistant":
            assert contract["input_schema"]["properties"]["message"]["maxLength"] == 2000
            assert contract["input_schema"]["additionalProperties"] is False
        else:
            assert contract["input_schema"]["discriminator"]["propertyName"] == "action"
    payroll = next(row for row in response.json() if row["name"] == "hr_payroll")
    assert payroll["actions"] == ["workflow.read"]


async def test_hr_migrations_reapply_and_gateway_readiness_use_shipped_schema(
    world: HrWorld,
) -> None:
    from apps.gateway.main import EXPECTED_SCHEMA_VERSION, check_readiness

    assert EXPECTED_SCHEMA_VERSION == "0430"
    before = await world.q("SELECT version FROM schema_migrations ORDER BY version")
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "scripts.migrate"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=30,
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "NO_LOCAL_ENV": "1",
            "ENV": "ci",
            "MIGRATE_DATABASE_URL": world.dsn,
            "CSA_APP_PASSWORD": "synthetic_hr_app",
            "CSA_RO_PASSWORD": "synthetic_hr_ro",
            "CSA_IDENTITY_PASSWORD": "synthetic_hr_identity",
        },
    )
    assert result.returncode == 0, result.stderr.decode()
    assert await world.q("SELECT version FROM schema_migrations ORDER BY version") == before
    assert (await check_readiness(world.service.pool)) == (True, "ok")


@pytest.mark.parametrize(
    "path", ["context", "employees", "training-requests", "agents", "agents/contracts", "workflows"]
)
async def test_new_hr_reads_never_accept_anonymous_or_machine_principals(
    world: HrWorld, path: str
) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=world.app), base_url=ORIGIN
    ) as browser:
        assert (await browser.get(f"/console/v1/hr/{path}")).status_code == 401
        assert (
            await browser.get(
                f"/console/v1/hr/{path}", headers={"Authorization": "Bearer synthetic"}
            )
        ).status_code == 401


async def test_same_user_cannot_confirm_from_a_different_valid_session(world: HrWorld) -> None:
    submitted = await world.submit()
    await switch(world, "hr_training")
    prepared = await world.call(
        "manager",
        "POST",
        "/console/v1/hr/agents/execute",
        {
            "action": "training.transition",
            "request_id": submitted["request_id"],
            "command": {"action": "approve", "expected_version": 1},
        },
    )
    assert prepared.status_code == 200, prepared.text
    new_token = f"synthetic-session-{uuid4()}"
    await world.q(
        "INSERT INTO console_sessions(id,user_id,token_hash,csrf_hash,client_id,expires_at) "
        "VALUES(%s,%s,%s,%s,%s,now()+interval '1 hour')",
        (
            uuid4(),
            world.users["manager"],
            digest(new_token),
            digest(world.csrf["manager"]),
            world.tenant,
        ),
    )
    world.browsers["manager"].cookies.set("csa_session", new_token)
    response = await world.call(
        "manager",
        "POST",
        f"/console/v1/hr/confirmations/{prepared.json()['confirmation_id']}/confirm",
        {},
    )
    assert response.status_code == 404, response.text


async def test_workflow_exact_prepare_replay_does_not_reveal_later_decision_reason(
    world: HrWorld,
) -> None:
    await workflow_grant(world, "staff", "leave", "prepare", "self")
    await workflow_grant(world, "owner", "leave", "decide", "client_owner")
    content = {
        "domain": "leave",
        "region": "sabah",
        "starts_on": "2026-11-01",
        "ends_on": "2026-11-01",
        "leave_type": "annual",
        "reason": "Synthetic",
    }
    key = uuid4()
    prepared = await world.call("staff", "POST", "/console/v1/hr/workflows", content, key=key)
    assert prepared.status_code == 201
    response = await world.call(
        "owner",
        "POST",
        f"/console/v1/hr/workflows/{prepared.json()['id']}/transitions",
        {"action": "reject", "expected_version": 0, "reason": "Synthetic reviewer reason"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision_reason"] == "Synthetic reviewer reason"
    replay = await world.call("staff", "POST", "/console/v1/hr/workflows", content, key=key)
    assert replay.json() == prepared.json()


async def test_actor_membership_reference_cannot_cross_tenant(world: HrWorld) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await world.q(
            "INSERT INTO hr_training_requests(client_id,id,employee_id,created_by,title,"
            "learning_objective,status,version,updated_at) "
            "VALUES (%s,%s,%s,%s,'Synthetic','Practice','draft',0,clock_timestamp())",
            (world.other_tenant, uuid4(), world.employees["staff"], world.users["staff"]),
        )


@pytest.mark.parametrize("injected", ["actor", "grants"])
async def test_transition_authority_injection_is_rejected_at_http_boundary(
    world: HrWorld, injected: str
) -> None:
    saved = await world.save()
    response = await world.call(
        "staff",
        "POST",
        f"/console/v1/hr/training-requests/{saved['request_id']}/transitions",
        {"action": "submit", "expected_version": 0, injected: "untrusted-authority"},
    )
    assert response.status_code == 422 and "untrusted-authority" not in response.text
    rows = await world.q(
        "SELECT status,version FROM hr_training_requests WHERE client_id=%s", (world.tenant,)
    )
    assert rows == [{"status": "draft", "version": 0}]


async def test_expiry_while_waiting_for_request_lock_denies_before_write(world: HrWorld) -> None:
    submitted = await world.submit()
    pool = db.build_pool(
        make_conninfo(world.dsn, user="csa_app", password="synthetic_hr_app"),
        min_size=1,
        max_size=1,
        autocommit=True,
    )
    await pool.open(wait=True)
    service = HrService(pool, session_idle_seconds=1800)
    operation = None
    try:
        async with pool.connection() as app_conn:
            async with app_conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT pg_backend_pid() AS pid")
                pid_row = await cursor.fetchone()
                assert pid_row is not None
                pid = pid_row["pid"]
        await world.q(
            "UPDATE console_sessions SET expires_at=clock_timestamp()+interval '2 seconds' "
            "WHERE id=%s",
            (world.sessions["manager"],),
        )
        async with await psycopg.AsyncConnection.connect(world.dsn, autocommit=True) as owner:
            async with owner.transaction():
                await owner.execute(
                    "SELECT 1 FROM hr_training_requests WHERE client_id=%s AND id=%s FOR UPDATE",
                    (world.tenant, UUID(submitted["request_id"])),
                )
                operation = asyncio.create_task(
                    service.transition(
                        world.proof("manager"),
                        UUID(submitted["request_id"]),
                        TrainingCommandInput(action=TrainingAction.APPROVE, expected_version=1),
                        uuid4(),
                    )
                )
                await _wait_for_database_lock(world, pid)
                deadline = asyncio.get_running_loop().time() + 5
                while True:
                    expired = (
                        await world.q(
                            "SELECT clock_timestamp()>=expires_at AS expired "
                            "FROM console_sessions WHERE id=%s",
                            (world.sessions["manager"],),
                        )
                    )[0]["expired"]
                    if expired:
                        break
                    assert asyncio.get_running_loop().time() < deadline
                    await asyncio.sleep(0.05)
            with pytest.raises(HrPolicyError, match="session_unavailable"):
                await asyncio.wait_for(operation, 10)
        rows = await world.q(
            "SELECT status,version FROM hr_training_requests WHERE client_id=%s", (world.tenant,)
        )
        assert rows == [{"status": "submitted", "version": 1}]
    finally:
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await pool.close()


def _revocation(world: HrWorld, kind: str, name: str) -> tuple[LiteralString, tuple[object, ...]]:
    if kind == "session":
        return "UPDATE console_sessions SET revoked_at=now() WHERE id=%s", (world.sessions[name],)
    if kind == "user":
        return "UPDATE console_users SET is_enabled=false WHERE id=%s", (world.users[name],)
    if kind == "membership":
        return (
            "UPDATE console_memberships SET is_enabled=false WHERE user_id=%s AND client_id=%s",
            (world.users[name], world.tenant),
        )
    if kind == "grant":
        return (
            "DELETE FROM hr_grants WHERE client_id=%s AND user_id=%s AND action='training.decide'",
            (world.tenant, world.users[name]),
        )
    if kind == "owner":
        return "DELETE FROM hr_client_owners WHERE client_id=%s", (world.tenant,)
    if kind == "link":
        return "DELETE FROM hr_employee_accounts WHERE client_id=%s AND user_id=%s", (
            world.tenant,
            world.users[name],
        )
    return (
        "UPDATE hr_employees SET manager_id=%s WHERE client_id=%s AND id=%s",
        (world.employees["outsider"], world.tenant, world.employees["staff"]),
    )


async def _wait_for_database_lock(world: HrWorld, pid: int) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while asyncio.get_running_loop().time() < deadline:
        row = (
            await world.q(
                "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=%s AND NOT granted) AS waiting",
                (pid,),
            )
        )[0]
        if row["waiting"]:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("expected contender never waited on the held database authority lock")


@pytest.mark.parametrize(
    "kind", ["session", "user", "membership", "grant", "owner", "manager", "link"]
)
async def test_domain_commit_holds_authority_until_commit(
    world: HrWorld, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    submitted = await world.submit()
    name = "owner" if kind == "owner" else "manager"
    entered, release = asyncio.Event(), asyncio.Event()
    original = world.service._record

    async def pause_before_commit(*args: Any, **kwargs: Any) -> None:
        await original(*args, **kwargs)
        entered.set()
        await asyncio.wait_for(release.wait(), 15)

    monkeypatch.setattr(world.service, "_record", pause_before_commit)
    operation = asyncio.create_task(
        world.service.transition(
            world.proof(name),
            UUID(submitted["request_id"]),
            TrainingCommandInput(action=TrainingAction.APPROVE, expected_version=1),
            uuid4(),
        )
    )
    revoker = None
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with await psycopg.AsyncConnection.connect(world.dsn, autocommit=True) as conn:
            pid_row = await (await conn.execute("SELECT pg_backend_pid()")).fetchone()
            assert pid_row is not None
            statement, params = _revocation(world, kind, name)
            revoker = asyncio.create_task(conn.execute(statement, params))
            try:
                await _wait_for_database_lock(world, pid_row[0])
                assert not revoker.done()
            finally:
                release.set()
            result = await asyncio.wait_for(operation, 10)
            assert result.request.status == "approved"
            await asyncio.wait_for(revoker, 10)
        with pytest.raises(HrPolicyError) as denied:
            await world.service.transition(
                world.proof(name),
                UUID(submitted["request_id"]),
                TrainingCommandInput(action=TrainingAction.APPROVE, expected_version=2),
                uuid4(),
            )
        assert denied.value.code in {
            "session_unavailable",
            "permission_denied",
            "resource_unavailable",
        }
    finally:
        release.set()
        await asyncio.gather(
            operation, *([revoker] if revoker is not None else []), return_exceptions=True
        )


@pytest.mark.parametrize(
    "kind", ["session", "user", "membership", "grant", "owner", "manager", "link"]
)
async def test_revocation_committing_first_denies_waiting_operation(
    world: HrWorld, kind: str
) -> None:
    submitted = await world.submit()
    name = "owner" if kind == "owner" else "manager"
    pool = db.build_pool(
        make_conninfo(world.dsn, user="csa_app", password="synthetic_hr_app"),
        min_size=1,
        max_size=1,
        autocommit=True,
    )
    await pool.open(wait=True)
    service = HrService(pool, session_idle_seconds=1800)
    operation = None
    try:
        async with pool.connection() as app_conn:
            async with app_conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute("SELECT pg_backend_pid() AS pid")
                pid_row = await cursor.fetchone()
            assert pid_row is not None
            pid = pid_row["pid"]
        async with await psycopg.AsyncConnection.connect(world.dsn, autocommit=True) as owner:
            async with owner.transaction():
                statement, params = _revocation(world, kind, name)
                await owner.execute(statement, params)
                operation = asyncio.create_task(
                    service.transition(
                        world.proof(name),
                        UUID(submitted["request_id"]),
                        TrainingCommandInput(action=TrainingAction.APPROVE, expected_version=1),
                        uuid4(),
                    )
                )
                await _wait_for_database_lock(world, pid)
            with pytest.raises(HrPolicyError) as denied:
                await asyncio.wait_for(operation, 10)
            assert denied.value.code in {
                "session_unavailable",
                "permission_denied",
                "resource_unavailable",
            }
        row = (
            await world.q(
                "SELECT status,version FROM hr_training_requests WHERE client_id=%s AND id=%s",
                (world.tenant, UUID(submitted["request_id"])),
            )
        )[0]
        assert row == {"status": "submitted", "version": 1}
    finally:
        if operation is not None:
            await asyncio.gather(operation, return_exceptions=True)
        await pool.close()
