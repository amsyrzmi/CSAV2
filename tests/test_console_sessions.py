"""Synthetic, same-origin console lifecycle and RBAC with real isolated pools."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, LiteralString

import httpx
import psycopg
import pytest
from csa.console.auth import build_identity_router
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.onboarding import build_onboarding_router
from csa.console.router import build_router
from csa.console.security import digest
from csa.core import db
from fastapi import FastAPI, HTTPException
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker, reset_console_identity

pytestmark = [requires_docker, pytest.mark.asyncio]
ORIGIN = "https://console.example.test"
INITIAL_PASSWORD = "synthetic-initial-password-123"
ADMIN_PASSWORD = "synthetic-admin-password-456"
MEMBER_PASSWORD = "synthetic-member-password-789"
MACHINE_TOKEN = "synthetic-read-only-machine-token-123456"


class SessionWorld:
    def __init__(
        self,
        owner_dsn: str,
        service: IdentityService,
        pool: AsyncConnectionPool,
        app: FastAPI,
        upload_root: Path,
    ) -> None:
        self.owner_dsn = owner_dsn
        self.service = service
        self.pool = pool
        self.app = app
        self.upload_root = upload_root
        self.browsers: list[httpx.AsyncClient] = []
        self.admin: httpx.AsyncClient
        self.admin_id: str
        self.tenants: list[str] = []
        self.initial_me: dict[str, Any] = {}
        self.initial_session = ""

    async def browser(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN)
        self.browsers.append(client)
        response = await client.get("/console/v1/auth/csrf")
        assert response.status_code == 200, response.text
        assert response.json()["csrf_token"] == client.cookies["csa_csrf"]
        return client

    @staticmethod
    def headers(client: httpx.AsyncClient) -> dict[str, str]:
        return {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["csa_csrf"]}

    async def mutate(
        self, client: httpx.AsyncClient, path: str, body: Any = None, *, method: str = "POST"
    ) -> httpx.Response:
        return await client.request(method, path, json=body, headers=self.headers(client))

    async def q(self, statement: LiteralString, *params: Any) -> list[dict[str, Any]]:
        async with await psycopg.AsyncConnection.connect(self.owner_dsn, autocommit=True) as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(statement, params)
                return await cursor.fetchall() if cursor.description else []

    async def invite(
        self, *, role: str = "tenant_staff", tenant: str | None = None
    ) -> dict[str, Any]:
        response = await self.mutate(
            self.admin,
            "/console/v1/admin/invites",
            {
                "client_id": tenant or self.tenants[0],
                "email": f"synthetic-{uuid.uuid4()}@example.test",
                "display_name": "Synthetic Member",
                "role": role,
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def member(
        self, *, role: str = "tenant_staff", tenant: str | None = None
    ) -> tuple[httpx.AsyncClient, dict[str, Any]]:
        invitation = await self.invite(role=role, tenant=tenant)
        client = await self.browser()
        response = await self.mutate(
            client,
            "/console/v1/invites/accept",
            {"token": invitation["token"], "password": MEMBER_PASSWORD},
        )
        assert response.status_code == 200, response.text
        return client, response.json()


@pytest.fixture
async def sessions(migrated_dsn: str, app_dsn: str, tmp_path: Path) -> AsyncIterator[SessionWorld]:
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await reset_console_identity(owner)
        await owner.execute(
            sql.SQL("ALTER ROLE csa_identity LOGIN PASSWORD {}").format(
                sql.Literal("synthetic_session_fixture_pw")
            )
        )
    identity_dsn = make_conninfo(
        migrated_dsn, user="csa_identity", password="synthetic_session_fixture_pw"
    )
    identity_pool = db.build_pool(identity_dsn, min_size=1, max_size=4, autocommit=True)
    domain_pool = db.build_pool(app_dsn, min_size=1, max_size=4, autocommit=True)
    await identity_pool.open(wait=True)
    await domain_pool.open(wait=True)
    service = IdentityService(identity_pool, IdentityConfig(origins=(ORIGIN,)))
    app = FastAPI()
    app.state.identity_service = service
    app.state.pool = domain_pool
    app.include_router(build_identity_router())
    app.include_router(build_onboarding_router())
    world = SessionWorld(migrated_dsn, service, domain_pool, app, tmp_path)
    try:
        await service.check_ready()
        world.admin_id = str(
            await service.bootstrap(
                "synthetic-admin@example.test", INITIAL_PASSWORD, "Synthetic Admin"
            )
        )
        world.admin = await world.browser()
        login = await world.mutate(
            world.admin,
            "/console/v1/auth/login",
            {"email": "synthetic-admin@example.test", "password": INITIAL_PASSWORD},
        )
        assert login.status_code == 200, login.text
        world.initial_me = login.json()
        world.initial_session = world.admin.cookies["csa_session"]
        blocked = await world.admin.get("/console/v1/admin/users")
        assert blocked.status_code == 403 and blocked.json()["detail"] == "password_change_required"
        changed = await world.mutate(
            world.admin,
            "/console/v1/auth/password/change",
            {"current_password": INITIAL_PASSWORD, "new_password": ADMIN_PASSWORD},
        )
        assert changed.status_code == 200, changed.text
        for _ in range(2):
            created = await world.mutate(
                world.admin,
                "/console/v1/admin/clients",
                {"slug": f"synthetic-{uuid.uuid4()}", "name": "Synthetic Session Tenant"},
            )
            assert created.status_code == 201, created.text
            world.tenants.append(created.json()["id"])
        app.include_router(
            build_router(
                MACHINE_TOKEN,
                machine_client_ids=frozenset({uuid.UUID(world.tenants[0])}),
                kb_upload_root=tmp_path,
            )
        )
        yield world
    finally:
        await asyncio.gather(*(client.aclose() for client in world.browsers))
        await identity_pool.close()
        await domain_pool.close()


async def test_bootstrap_forces_password_change_and_closes_after_first_admin(
    sessions: SessionWorld,
) -> None:
    assert sessions.initial_me["user"]["must_change_password"] is True
    assert sessions.initial_me["role"] == "platform_admin"
    assert sessions.admin.cookies["csa_session"] != sessions.initial_session
    browser = await sessions.browser()
    browser.cookies.set("csa_session", sessions.initial_session)
    assert (await browser.get("/console/v1/auth/me")).status_code == 401
    with pytest.raises(ValueError, match="bootstrap is closed"):
        await sessions.service.bootstrap("other@example.test", ADMIN_PASSWORD, "Another Admin")
    me = await sessions.admin.get("/console/v1/auth/me")
    assert me.status_code == 200 and me.json()["user"]["must_change_password"] is False
    denied = await sessions.mutate(
        sessions.admin,
        f"/console/v1/admin/users/{sessions.admin_id}",
        {"is_enabled": False},
        method="PATCH",
    )
    assert denied.status_code == 409


async def test_login_me_logout_cookie_policy_and_hashed_persistence(sessions: SessionWorld) -> None:
    client, member = await sessions.member()
    token = client.cookies["csa_session"]
    session_cookie = next(cookie for cookie in client.cookies.jar if cookie.name == "csa_session")
    csrf_cookie = next(cookie for cookie in client.cookies.jar if cookie.name == "csa_csrf")
    assert session_cookie.secure and session_cookie.has_nonstandard_attr("HttpOnly")
    assert session_cookie.get_nonstandard_attr("SameSite") == "lax"
    assert csrf_cookie.secure and not csrf_cookie.has_nonstandard_attr("HttpOnly")
    rows = await sessions.q(
        "SELECT s.token_hash,s.csrf_hash,u.password_hash FROM console_sessions s "
        "JOIN console_users u ON u.id=s.user_id WHERE s.token_hash=%s",
        digest(token),
    )
    assert len(rows) == 1
    assert rows[0]["token_hash"] != token and rows[0]["csrf_hash"] != client.cookies["csa_csrf"]
    assert MEMBER_PASSWORD not in rows[0]["password_hash"]
    me = await client.get("/console/v1/auth/me")
    assert me.json()["user"]["id"] == member["user"]["id"]
    assert me.headers["Cache-Control"] == "no-store"
    assert "password_hash" not in me.text and "token_hash" not in me.text
    logout = await sessions.mutate(client, "/console/v1/auth/logout")
    assert logout.status_code == 204
    assert "csa_session" not in client.cookies
    assert (await client.get("/console/v1/auth/me")).status_code == 401
    assert (
        await sessions.q(
            "SELECT revoked_at FROM console_sessions WHERE token_hash=%s", digest(token)
        )
    )[0]["revoked_at"]
    audit = await sessions.q(
        "SELECT action,details::text AS details FROM console_audit WHERE actor_id=%s",
        member["user"]["id"],
    )
    assert {row["action"] for row in audit} >= {"identity.invite_accepted", "identity.logout"}
    assert all(
        token not in row["details"] and MEMBER_PASSWORD not in row["details"] for row in audit
    )


async def test_me_reports_the_workspace_assistant_flag(sessions: SessionWorld) -> None:
    me = await sessions.admin.get("/console/v1/auth/me")
    assert me.status_code == 200 and me.json()["workspace_assistant"] is False
    default = sessions.service.config
    sessions.service.config = default.model_copy(update={"workspace_assistant": True})
    try:
        me = await sessions.admin.get("/console/v1/auth/me")
        assert me.status_code == 200 and me.json()["workspace_assistant"] is True
    finally:
        sessions.service.config = default


async def test_password_change_supports_unicode_and_rejects_reusing_it(
    sessions: SessionWorld,
) -> None:
    client, member = await sessions.member()
    password = "synthetic-password-\u00e9-123"
    changed = await sessions.mutate(
        client,
        "/console/v1/auth/password/change",
        {"current_password": MEMBER_PASSWORD, "new_password": password},
    )
    assert changed.status_code == 200, changed.text
    reused = await sessions.mutate(
        client,
        "/console/v1/auth/password/change",
        {"current_password": password, "new_password": password},
    )
    assert reused.status_code == 422
    assert reused.json()["detail"] == "new password must differ from the current password"
    changed_again = await sessions.mutate(
        client,
        "/console/v1/auth/password/change",
        {"current_password": password, "new_password": MEMBER_PASSWORD},
    )
    assert changed_again.status_code == 200, changed_again.text
    assert changed_again.json()["user"]["id"] == member["user"]["id"]


@pytest.mark.parametrize("kind", ["absolute", "idle", "revoked", "disabled"])
async def test_expired_revoked_and_disabled_sessions_are_rejected(
    sessions: SessionWorld, kind: str
) -> None:
    client, member = await sessions.member()
    token_hash = digest(client.cookies["csa_session"])
    if kind == "absolute":
        await sessions.q(
            "UPDATE console_sessions SET created_at=now()-interval '2 days',"
            "expires_at=now()-interval '1 day' WHERE token_hash=%s",
            token_hash,
        )
    elif kind == "idle":
        await sessions.q(
            "UPDATE console_sessions SET last_seen_at=now()-interval '2 hours' WHERE token_hash=%s",
            token_hash,
        )
    elif kind == "revoked":
        revoked = await sessions.mutate(
            sessions.admin, f"/console/v1/admin/users/{member['user']['id']}/revoke-sessions"
        )
        assert revoked.status_code == 204
    else:
        disabled = await sessions.mutate(
            sessions.admin,
            f"/console/v1/admin/users/{member['user']['id']}",
            {"is_enabled": False},
            method="PATCH",
        )
        assert disabled.status_code == 204
    assert (await client.get("/console/v1/auth/me")).status_code == 401


async def test_login_errors_are_generic_and_throttle_persists_across_services(
    sessions: SessionWorld,
) -> None:
    client, member = await sessions.member()
    await sessions.mutate(
        sessions.admin,
        f"/console/v1/admin/users/{member['user']['id']}",
        {"is_enabled": False},
        method="PATCH",
    )
    anonymous = await sessions.browser()
    responses = []
    for email, password in (
        ("missing@example.test", MEMBER_PASSWORD),
        ("synthetic-admin@example.test", "incorrect-password-123"),
        (member["user"]["email"], MEMBER_PASSWORD),
    ):
        response = await sessions.mutate(
            anonymous, "/console/v1/auth/login", {"email": email, "password": password}
        )
        assert response.status_code == 401
        responses.append(response.json())
    assert responses[0] == responses[1] == responses[2]
    for _ in range(5):
        denied = await sessions.mutate(
            anonymous,
            "/console/v1/auth/login",
            {"email": "throttled@example.test", "password": MEMBER_PASSWORD},
        )
        assert denied.status_code == 401
    sessions.app.state.identity_service = IdentityService(
        sessions.service.pool, sessions.service.config
    )
    blocked = await sessions.mutate(
        anonymous,
        "/console/v1/auth/login",
        {"email": "throttled@example.test", "password": MEMBER_PASSWORD},
    )
    assert blocked.status_code == 429 and int(blocked.headers["Retry-After"]) > 0


@pytest.mark.parametrize(
    "attack", ["missing-origin", "wrong-origin", "missing-token", "wrong-token", "unbound-token"]
)
async def test_csrf_origin_and_session_binding_reject_mutations(
    sessions: SessionWorld, attack: str
) -> None:
    headers = sessions.headers(sessions.admin)
    if attack == "missing-origin":
        headers.pop("Origin")
    elif attack == "wrong-origin":
        headers["Origin"] = "https://attacker.example.test"
    elif attack == "missing-token":
        headers.pop("X-CSRF-Token")
    elif attack == "wrong-token":
        headers["X-CSRF-Token"] = "forged-token"
    else:
        sessions.admin.cookies.set("csa_csrf", "forged-double-submit-token")
        headers["X-CSRF-Token"] = "forged-double-submit-token"
    response = await sessions.admin.post("/console/v1/auth/logout", headers=headers)
    assert response.status_code == 403
    assert (await sessions.admin.get("/console/v1/auth/me")).status_code == 200


@pytest.mark.parametrize(
    "header", ["X-Client-Id", "X-Actor-Id", "X-Actor-Role", "X-User-Id", "X-Role"]
)
async def test_forged_identity_headers_are_rejected(sessions: SessionWorld, header: str) -> None:
    browser, _ = await sessions.member(role="read_only")
    for path in ("/console/v1/auth/me", "/console/v1/capabilities"):
        response = await browser.get(path, headers={header: sessions.tenants[1]})
        assert response.status_code in {400, 403}


async def test_invite_and_reset_are_single_use_rotate_sessions_and_do_not_leak_tokens(
    sessions: SessionWorld,
) -> None:
    invitation = await sessions.invite(role="tenant_admin")
    client = await sessions.browser()
    inspected = await sessions.mutate(
        client, "/console/v1/invites/inspect", {"token": invitation["token"]}
    )
    assert inspected.status_code == 200 and inspected.json()["kind"] == "invite"
    assert invitation["token"] not in inspected.text
    accepted = await sessions.mutate(
        client,
        "/console/v1/invites/accept",
        {"token": invitation["token"], "password": MEMBER_PASSWORD},
    )
    assert accepted.status_code == 200 and accepted.json()["role"] == "tenant_admin"
    replay = await sessions.mutate(
        client,
        "/console/v1/invites/accept",
        {"token": invitation["token"], "password": MEMBER_PASSWORD},
    )
    assert replay.status_code == 400
    user_id = accepted.json()["user"]["id"]
    old_session = client.cookies["csa_session"]
    reset = await sessions.mutate(sessions.admin, f"/console/v1/admin/users/{user_id}/reset")
    assert reset.status_code == 201 and reset.json()["delivery"] == "manual"
    assert (await client.get("/console/v1/auth/me")).status_code == 401
    reset_browser = await sessions.browser()
    changed = await sessions.mutate(
        reset_browser,
        "/console/v1/invites/accept",
        {"token": reset.json()["token"], "password": "synthetic-new-member-password-000"},
    )
    assert changed.status_code == 200 and reset_browser.cookies["csa_session"] != old_session
    replay = await sessions.mutate(
        reset_browser, "/console/v1/invites/inspect", {"token": reset.json()["token"]}
    )
    assert replay.status_code == 400
    persisted = await sessions.q(
        "SELECT token_hash,consumed_at FROM console_tokens WHERE id=%s", invitation["id"]
    )
    assert persisted[0]["token_hash"] == digest(invitation["token"]) and persisted[0]["consumed_at"]


async def test_accepting_an_invitation_revokes_the_previous_browser_session(
    sessions: SessionWorld,
) -> None:
    browser, previous_identity = await sessions.member()
    previous_token = browser.cookies["csa_session"]
    invitation = await sessions.invite()
    accepted = await sessions.mutate(
        browser,
        "/console/v1/invites/accept",
        {"token": invitation["token"], "password": MEMBER_PASSWORD},
    )
    assert accepted.status_code == 200
    assert accepted.json()["user"]["id"] != previous_identity["user"]["id"]
    previous = await sessions.q(
        "SELECT revoked_at FROM console_sessions WHERE token_hash=%s", digest(previous_token)
    )
    assert previous[0]["revoked_at"] is not None
    replay = await sessions.browser()
    replay.cookies.set("csa_session", previous_token)
    assert (await replay.get("/console/v1/auth/me")).status_code == 401


async def test_password_reset_cannot_bypass_pending_invitation_membership(
    sessions: SessionWorld,
) -> None:
    invitation = await sessions.invite(role="tenant_admin")
    [pending] = await sessions.q("SELECT user_id FROM console_tokens WHERE id=%s", invitation["id"])
    reset = await sessions.mutate(
        sessions.admin, f"/console/v1/admin/users/{pending['user_id']}/reset"
    )
    assert reset.status_code == 409
    [state] = await sessions.q(
        "SELECT revoked_at,consumed_at FROM console_tokens WHERE id=%s", invitation["id"]
    )
    assert state == {"revoked_at": None, "consumed_at": None}
    assert not await sessions.q(
        "SELECT id FROM console_tokens WHERE user_id=%s AND kind='reset'", pending["user_id"]
    )
    client = await sessions.browser()
    accepted = await sessions.mutate(
        client,
        "/console/v1/invites/accept",
        {"token": invitation["token"], "password": MEMBER_PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["role"] == "tenant_admin"
    assert accepted.json()["active_client_id"] == sessions.tenants[0]


@pytest.mark.parametrize("kind", ["expired", "revoked", "concurrent"])
async def test_invitation_expiry_revocation_and_concurrent_consumption(
    sessions: SessionWorld, kind: str
) -> None:
    invitation = await sessions.invite()
    if kind == "expired":
        await sessions.q(
            "UPDATE console_tokens SET expires_at=now()-interval '1 second' WHERE id=%s",
            invitation["id"],
        )
    elif kind == "revoked":
        revoked = await sessions.mutate(
            sessions.admin, f"/console/v1/admin/invites/{invitation['id']}", method="DELETE"
        )
        assert revoked.status_code == 204
    browsers = [await sessions.browser() for _ in range(2 if kind == "concurrent" else 1)]
    responses = await asyncio.gather(
        *(
            sessions.mutate(
                browser,
                "/console/v1/invites/accept",
                {"token": invitation["token"], "password": MEMBER_PASSWORD},
            )
            for browser in browsers
        )
    )
    assert sorted(response.status_code for response in responses) == (
        [200, 400] if kind == "concurrent" else [400]
    )


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
async def test_tenant_members_cannot_select_another_tenant_or_escalate(
    sessions: SessionWorld, role: str
) -> None:
    client, member = await sessions.member(role=role)
    assert member["active_client_id"] == sessions.tenants[0] and member["role"] == role
    own = await client.get("/console/v1/profile")
    assert own.status_code == 200 and own.json()["id"] == sessions.tenants[0]
    foreign = await client.get("/console/v1/profile", params={"client_id": sessions.tenants[1]})
    assert foreign.status_code == 403
    assert (await client.get("/console/v1/admin/clients")).status_code == 403
    assert (await client.get("/console/v1/admin/users")).status_code == 403
    denied = await sessions.mutate(
        client, "/console/v1/capabilities/kb_agent", {"is_enabled": True}, method="PATCH"
    )
    assert denied.status_code == 403
    escalated = await sessions.mutate(
        client,
        "/console/v1/team/invites",
        {
            "client_id": sessions.tenants[0],
            "email": "escalation@example.test",
            "display_name": "Escalation Attempt",
            "role": "platform_admin",
        },
    )
    assert escalated.status_code == 422
    switched = await sessions.mutate(
        client, "/console/v1/capabilities/kb_agent", {"client_enabled": True}, method="PATCH"
    )
    assert switched.status_code == (200 if role == "tenant_admin" else 403)
    foreign_team = await client.get("/console/v1/team", params={"client_id": sessions.tenants[1]})
    assert foreign_team.status_code == 403


async def test_member_lifecycle_preserves_last_admin_and_rejects_stale_sessions(
    sessions: SessionWorld,
) -> None:
    admin, member = await sessions.member(role="tenant_admin")
    path = f"/console/v1/team/{member['user']['id']}?client_id={sessions.tenants[0]}"
    denied = await sessions.mutate(admin, path, {"role": "tenant_staff"}, method="PATCH")
    assert denied.status_code == 409
    staff, staff_me = await sessions.member()
    updated = await sessions.mutate(
        admin,
        f"/console/v1/team/{staff_me['user']['id']}?client_id={sessions.tenants[0]}",
        {"role": "read_only"},
        method="PATCH",
    )
    assert updated.status_code == 204
    assert (await staff.get("/console/v1/profile")).status_code == 401
    blocked = await sessions.mutate(
        admin, f"/console/v1/admin/users/{staff_me['user']['id']}/reset"
    )
    assert blocked.status_code == 403
    reissued = await sessions.mutate(
        admin,
        "/console/v1/team/invites",
        {
            "client_id": sessions.tenants[0],
            "email": staff_me["user"]["email"],
            "display_name": "Cannot Replace Password",
            "role": "tenant_admin",
        },
    )
    assert reissued.status_code == 409


async def test_tenant_onboarding_provisions_disabled_capabilities_and_atomic_audit(
    sessions: SessionWorld,
) -> None:
    from csa.graph.policy.gate import Policy

    [tenant] = await sessions.q("SELECT config FROM clients WHERE id=%s", sessions.tenants[0])
    assert tenant["config"]["runtime"] == "langgraph"
    assert Policy.model_validate(tenant["config"]["policy"]).deployment_mode == "shadow"
    rows = await sessions.q(
        "SELECT name,is_enabled,client_enabled,input_schema FROM capability_registry "
        "WHERE client_id=%s",
        sessions.tenants[0],
    )
    assert {row["name"] for row in rows} >= {
        "kb_agent",
        "order_agent",
        "booking_agent",
        "subscription_agent",
    }
    assert all(not row["is_enabled"] and not row["client_enabled"] for row in rows)
    assert all(row["input_schema"].get("type") == "object" for row in rows)
    created = await sessions.q(
        "SELECT actor_id,details FROM console_audit WHERE client_id=%s AND action='tenant.created'",
        sessions.tenants[0],
    )
    assert len(created) == 1 and str(created[0]["actor_id"]) == sessions.admin_id
    assert created[0]["details"]["capabilities_enabled"] is False
    enabled = await sessions.mutate(
        sessions.admin,
        f"/console/v1/capabilities/kb_agent?client_id={sessions.tenants[0]}",
        {"is_enabled": True},
        method="PATCH",
    )
    assert enabled.status_code == 200
    assert (await sessions.admin.get("/console/v1/capabilities")).status_code == 400
    forged = await sessions.mutate(
        sessions.admin,
        f"/console/v1/capabilities/kb_agent?client_id={sessions.tenants[0]}",
        {"client_enabled": True, "changed_by": "forged-admin"},
        method="PATCH",
    )
    assert forged.status_code == 422
    audit = await sessions.q(
        "SELECT actor_id,details FROM console_audit "
        "WHERE client_id=%s AND action='capability.updated'",
        sessions.tenants[0],
    )
    assert len(audit) == 1 and str(audit[0]["actor_id"]) == sessions.admin_id


async def test_machine_auth_is_read_only_allowlisted_and_separate_from_human_sessions(
    sessions: SessionWorld,
) -> None:
    machine = httpx.AsyncClient(transport=httpx.ASGITransport(app=sessions.app), base_url=ORIGIN)
    try:
        headers = {"Authorization": f"Bearer {MACHINE_TOKEN}", "X-Client-Id": sessions.tenants[0]}
        assert (await machine.get("/console/v1/capabilities", headers=headers)).status_code == 200
        headers["X-Client-Id"] = sessions.tenants[1]
        assert (await machine.get("/console/v1/capabilities", headers=headers)).status_code == 403
        denied = await machine.patch(
            "/console/v1/capabilities/kb_agent", json={"is_enabled": True}, headers=headers
        )
        assert denied.status_code == 403
        assert (await machine.get("/console/v1/auth/me", headers=headers)).status_code == 403
        mixed = await sessions.admin.get("/console/v1/capabilities", headers=headers)
        assert mixed.status_code == 400
    finally:
        await machine.aclose()


async def test_audit_failure_rolls_back_tenant_creation_and_capability_change(
    sessions: SessionWorld,
) -> None:
    slug = f"synthetic-rollback-{uuid.uuid4()}"
    await sessions.q("REVOKE INSERT ON console_audit FROM csa_app")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await sessions.mutate(
                sessions.admin, "/console/v1/admin/clients", {"slug": slug, "name": "Rollback"}
            )
        assert await sessions.q("SELECT id FROM clients WHERE slug=%s", slug) == []
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await sessions.mutate(
                sessions.admin,
                f"/console/v1/capabilities/kb_agent?client_id={sessions.tenants[0]}",
                {"is_enabled": True},
                method="PATCH",
            )
        [capability] = await sessions.q(
            "SELECT is_enabled FROM capability_registry WHERE client_id=%s AND name='kb_agent'",
            sessions.tenants[0],
        )
        assert capability["is_enabled"] is False
    finally:
        await sessions.q("GRANT INSERT ON console_audit TO csa_app")


@pytest.mark.parametrize("role", ["tenant_admin", "tenant_staff", "read_only"])
async def test_m2_catalogue_permissions_tenant_scope_and_switch_policy(
    sessions: SessionWorld, role: str
) -> None:
    browser, _ = await sessions.member(role=role)
    response = await browser.get("/console/v1/agents")
    assert response.status_code == 200
    catalog = {row["name"]: row for row in response.json()}
    assert catalog["booking_agent"]["implementation"] == "prepared_only"
    assert catalog["invoice_agent"]["can_toggle"] is (role == "tenant_admin")
    assert catalog["invoice_agent"]["can_subscribe"] is False
    assert (
        await browser.get("/console/v1/agents", params={"client_id": sessions.tenants[1]})
    ).status_code == 403
    attempted = await sessions.mutate(
        browser, "/console/v1/capabilities/invoice_agent", {"is_enabled": True}, method="PATCH"
    )
    assert attempted.status_code == 403
    tenant_toggle = await sessions.mutate(
        browser, "/console/v1/capabilities/invoice_agent", {"client_enabled": True}, method="PATCH"
    )
    assert tenant_toggle.status_code == (200 if role == "tenant_admin" else 403)
    safety = await sessions.mutate(
        sessions.admin,
        f"/console/v1/capabilities/escalation_agent?client_id={sessions.tenants[0]}",
        {"is_enabled": False},
        method="PATCH",
    )
    assert safety.status_code == 409


async def test_m2_runtime_keyset_pagination_detail_redaction_and_overview(
    sessions: SessionWorld,
) -> None:
    reader, _ = await sessions.member(role="read_only")
    other, _ = await sessions.member(role="read_only", tenant=sessions.tenants[1])
    ids: list[int] = []
    stamp = datetime.now(UTC)
    for index in range(4):
        [row] = await sessions.q(
            "INSERT INTO agent_runs(client_id,trace_id,agent,ok,status,input,output,created_at) "
            'VALUES (%s,%s,%s,%s,%s,\'{"private":"synthetic-secret"}\','
            '\'{"private":"synthetic-output"}\',%s) RETURNING id',
            sessions.tenants[0],
            uuid.uuid4(),
            "invoice_agent" if index < 3 else "booking_agent",
            index != 1,
            "error" if index == 1 else "ok",
            stamp,
        )
        ids.append(row["id"])
    summary = await reader.get("/console/v1/overview")
    assert summary.status_code == 200
    assert summary.json()["runs"] == 4
    assert summary.json()["failed"] == 1
    assert (await other.get("/console/v1/overview")).json()["runs"] == 0
    first = await reader.get("/console/v1/runs", params={"limit": 2})
    assert first.status_code == 200
    assert [row["id"] for row in first.json()["items"]] == ids[::-1][:2]
    cursor = first.json()["next_before_id"]
    second = await reader.get("/console/v1/runs", params={"limit": 2, "before_id": cursor})
    assert [row["id"] for row in second.json()["items"]] == ids[::-1][2:]
    assert second.json()["next_before_id"] is None
    filtered = await reader.get("/console/v1/runs", params={"agent": "invoice_agent"})
    assert len(filtered.json()["items"]) == 3
    detail = await reader.get(f"/console/v1/runs/{ids[0]}")
    assert detail.status_code == 200
    assert "input" not in detail.json() and "output" not in detail.json()
    assert "synthetic-secret" not in first.text + detail.text
    assert (await other.get("/console/v1/runs")).json()["items"] == []
    assert (await other.get(f"/console/v1/runs/{ids[0]}")).status_code == 404
    for params in (
        {"limit": 0},
        {"limit": 201},
        {"before_id": 0},
        {"before_id": 2**63},
        {"agent": ""},
    ):
        assert (await reader.get("/console/v1/runs", params=params)).status_code == 422
    for invalid_id in (0, -1, 2**63):
        assert (await reader.get(f"/console/v1/runs/{invalid_id}")).status_code == 422
    assert (
        await reader.get("/console/v1/runs", params={"client_id": sessions.tenants[1]})
    ).status_code == 403
    assert (await sessions.admin.get("/console/v1/runs")).status_code == 400


async def test_m2_kb_console_execution_cannot_bypass_disabled_switch(
    sessions: SessionWorld,
) -> None:
    from tests.test_console_services import activate

    # The package is active, so the 409 comes from the switch, not the package gate.
    tenant = sessions.tenants[0]
    await activate(sessions, tenant, "customer_support")
    off = await sessions.mutate(
        sessions.admin,
        f"/console/v1/capabilities/kb_agent?client_id={tenant}",
        {"is_enabled": False},
        method="PATCH",
    )
    assert off.status_code == 200, off.text
    admin, _ = await sessions.member(role="tenant_admin")
    response = await sessions.mutate(
        admin, "/console/v1/kb/documents", {"title": "Synthetic", "content": "Synthetic"}
    )
    assert response.status_code == 409
    assert "disabled" in response.json()["detail"]
    response = await sessions.mutate(admin, f"/console/v1/kb/gaps/{uuid.uuid4()}/resolve")
    assert response.status_code == 409
    assert (
        await admin.request(
            "DELETE", f"/console/v1/kb/documents/{uuid.uuid4()}", headers=sessions.headers(admin)
        )
    ).status_code == 409


async def test_password_change_checks_current_password_and_revokes_other_sessions(
    sessions: SessionWorld,
) -> None:
    first, member = await sessions.member()
    other = await sessions.browser()
    login = await sessions.mutate(
        other,
        "/console/v1/auth/login",
        {
            "email": member["user"]["email"],
            "password": MEMBER_PASSWORD,
        },
    )
    assert login.status_code == 200
    before = first.cookies["csa_session"]
    wrong = await sessions.mutate(
        first,
        "/console/v1/auth/password/change",
        {
            "current_password": "synthetic-wrong-current-password",
            "new_password": ADMIN_PASSWORD,
        },
    )
    assert wrong.status_code == 401
    assert (await other.get("/console/v1/auth/me")).status_code == 200
    changed = await sessions.mutate(
        first,
        "/console/v1/auth/password/change",
        {
            "current_password": MEMBER_PASSWORD,
            "new_password": ADMIN_PASSWORD,
        },
    )
    assert changed.status_code == 200 and first.cookies["csa_session"] != before
    assert (await other.get("/console/v1/auth/me")).status_code == 401


async def test_invitation_renewal_replaces_old_token_and_list_omits_token_material(
    sessions: SessionWorld,
) -> None:
    invitation = await sessions.invite()
    await sessions.q(
        "UPDATE console_tokens SET expires_at=now()-interval '1 second' WHERE id=%s",
        invitation["id"],
    )
    renewed = await sessions.mutate(
        sessions.admin, f"/console/v1/admin/invites/{invitation['id']}/renew"
    )
    assert renewed.status_code == 201
    assert renewed.json()["token"] != invitation["token"]
    listing = await sessions.admin.get(
        "/console/v1/admin/invites", params={"client_id": sessions.tenants[0]}
    )
    assert listing.status_code == 200
    assert "token_hash" not in listing.text and renewed.json()["token"] not in listing.text
    browser = await sessions.browser()
    old = await sessions.mutate(
        browser, "/console/v1/invites/inspect", {"token": invitation["token"]}
    )
    current = await sessions.mutate(
        browser, "/console/v1/invites/inspect", {"token": renewed.json()["token"]}
    )
    assert old.status_code == 400 and current.status_code == 200


async def test_public_reset_response_is_generic_and_creates_no_retrievable_token(
    sessions: SessionWorld,
) -> None:
    anonymous = await sessions.browser()
    before = await sessions.q("SELECT count(*) AS count FROM console_tokens")
    existing = await sessions.mutate(
        anonymous, "/console/v1/auth/password/reset", {"email": "synthetic-admin@example.test"}
    )
    missing = await sessions.mutate(
        anonymous, "/console/v1/auth/password/reset", {"email": "missing@example.test"}
    )
    assert existing.status_code == missing.status_code == 202
    assert existing.json() == missing.json()
    assert "token" not in existing.text
    assert await sessions.q("SELECT count(*) AS count FROM console_tokens") == before


async def _audit_actions(owner_dsn: str, action: str) -> list[dict[str, Any]]:
    async with await psycopg.AsyncConnection.connect(owner_dsn) as conn:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT actor_id, target_id, details FROM console_audit WHERE action = %s",
                (action,),
            )
            return await cur.fetchall()


async def test_add_platform_admin_requires_a_verified_enabled_approver(
    sessions: SessionWorld,
) -> None:
    service = sessions.service
    with pytest.raises(PermissionError):
        await service.add_platform_admin(
            "synthetic-admin@example.test",
            "wrong-password-000",
            "ops@example.test",
            "Ops",
            ADMIN_PASSWORD,
        )
    with pytest.raises(PermissionError):
        await service.add_platform_admin(
            "nobody@example.test", ADMIN_PASSWORD, "ops@example.test", "Ops", ADMIN_PASSWORD
        )
    refused = await _audit_actions(sessions.owner_dsn, "identity.platform_admin_add_refused")
    assert len(refused) == 2 and all(row["actor_id"] is None for row in refused)
    assert all(row["details"] in ({}, None) for row in refused)
    assert {str(row["target_id"]) for row in refused} == {
        digest("synthetic-admin@example.test"),
        digest("nobody@example.test"),
    }
    async with await psycopg.AsyncConnection.connect(sessions.owner_dsn) as conn:
        count = await (await conn.execute("SELECT count(*) FROM console_users")).fetchone()
    assert count is not None and count[0] == 1


async def test_add_platform_admin_creates_a_forced_change_admin_once(
    sessions: SessionWorld,
) -> None:
    service = sessions.service
    new_id = await service.add_platform_admin(
        "synthetic-admin@example.test",
        ADMIN_PASSWORD,
        "Ops@Example.test ",
        "Ops Admin",
        MEMBER_PASSWORD,
    )
    added = await _audit_actions(sessions.owner_dsn, "identity.platform_admin_added")
    assert [str(row["target_id"]) for row in added] == [str(new_id)]
    assert str(added[0]["actor_id"]) == sessions.admin_id
    assert added[0]["details"] == {"approver_email": "synthetic-admin@example.test"}
    browser = await sessions.browser()
    login = await sessions.mutate(
        browser,
        "/console/v1/auth/login",
        {"email": "ops@example.test", "password": MEMBER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert login.json()["role"] == "platform_admin"
    assert login.json()["user"]["must_change_password"] is True
    with pytest.raises(ValueError, match="already exists"):
        await service.add_platform_admin(
            "synthetic-admin@example.test",
            ADMIN_PASSWORD,
            "ops@example.test",
            "Again",
            MEMBER_PASSWORD,
        )


async def test_add_platform_admin_throttles_repeated_bad_approver_passwords(
    sessions: SessionWorld,
) -> None:
    service = sessions.service
    for _ in range(service.config.login_attempts):
        with pytest.raises(PermissionError):
            await service.add_platform_admin(
                "synthetic-admin@example.test",
                "wrong-password-000",
                "ops@example.test",
                "Ops",
                ADMIN_PASSWORD,
            )
    with pytest.raises(HTTPException) as blocked:
        await service.add_platform_admin(
            "synthetic-admin@example.test",
            ADMIN_PASSWORD,
            "ops@example.test",
            "Ops",
            ADMIN_PASSWORD,
        )
    assert blocked.value.status_code == 429


async def _user_count(owner_dsn: str) -> int:
    async with await psycopg.AsyncConnection.connect(owner_dsn) as conn:
        row = await (await conn.execute("SELECT count(*) FROM console_users")).fetchone()
    assert row is not None
    return int(row[0])


async def test_add_platform_admin_refuses_a_disabled_approver(sessions: SessionWorld) -> None:
    before = await _user_count(sessions.owner_dsn)
    await sessions.q("UPDATE console_users SET is_enabled = false WHERE id = %s", sessions.admin_id)
    with pytest.raises(PermissionError):
        await sessions.service.add_platform_admin(
            "synthetic-admin@example.test",
            ADMIN_PASSWORD,
            "ops@example.test",
            "Ops",
            ADMIN_PASSWORD,
        )
    assert await _user_count(sessions.owner_dsn) == before
    refused = await _audit_actions(sessions.owner_dsn, "identity.platform_admin_add_refused")
    assert [str(row["target_id"]) for row in refused] == [digest("synthetic-admin@example.test")]
    assert all(row["details"] in ({}, None) for row in refused)


async def test_add_platform_admin_refuses_a_tenant_member_approver(
    sessions: SessionWorld,
) -> None:
    _, me = await sessions.member()
    email = me["user"]["email"]
    before = await _user_count(sessions.owner_dsn)
    with pytest.raises(PermissionError):
        await sessions.service.add_platform_admin(
            email, MEMBER_PASSWORD, "ops@example.test", "Ops", ADMIN_PASSWORD
        )
    assert await _user_count(sessions.owner_dsn) == before
    refused = await _audit_actions(sessions.owner_dsn, "identity.platform_admin_add_refused")
    assert [str(row["target_id"]) for row in refused] == [digest(email)]


async def test_add_platform_admin_refuses_an_approver_who_must_change_password(
    sessions: SessionWorld,
) -> None:
    await sessions.service.add_platform_admin(
        "synthetic-admin@example.test", ADMIN_PASSWORD, "ops@example.test", "Ops", MEMBER_PASSWORD
    )
    before = await _user_count(sessions.owner_dsn)
    with pytest.raises(PermissionError):
        await sessions.service.add_platform_admin(
            "ops@example.test", MEMBER_PASSWORD, "second@example.test", "Second", ADMIN_PASSWORD
        )
    assert await _user_count(sessions.owner_dsn) == before


async def test_add_platform_admin_refuses_an_existing_tenant_member_email(
    sessions: SessionWorld,
) -> None:
    _, me = await sessions.member()
    before = await _user_count(sessions.owner_dsn)
    with pytest.raises(ValueError, match="already exists"):
        await sessions.service.add_platform_admin(
            "synthetic-admin@example.test",
            ADMIN_PASSWORD,
            me["user"]["email"],
            "Clash",
            ADMIN_PASSWORD,
        )
    assert await _user_count(sessions.owner_dsn) == before


async def test_add_platform_admin_rechecks_the_approver_inside_the_transaction(
    sessions: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    import csa.console.identity as identity_module

    real_hash = identity_module.hash_password

    async def disable_then_hash(password: str) -> str:
        await sessions.q(
            "UPDATE console_users SET is_enabled = false WHERE id = %s", sessions.admin_id
        )
        return await real_hash(password)

    before = await _user_count(sessions.owner_dsn)
    monkeypatch.setattr(identity_module, "hash_password", disable_then_hash)
    with pytest.raises(PermissionError):
        await sessions.service.add_platform_admin(
            "synthetic-admin@example.test",
            ADMIN_PASSWORD,
            "ops@example.test",
            "Ops",
            ADMIN_PASSWORD,
        )
    assert await _user_count(sessions.owner_dsn) == before
    refused = await _audit_actions(sessions.owner_dsn, "identity.platform_admin_add_refused")
    assert [str(row["target_id"]) for row in refused] == [digest("synthetic-admin@example.test")]
