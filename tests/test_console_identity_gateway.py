"""Explicit identity-pool startup, shutdown, readiness and API exposure."""

from __future__ import annotations

import asyncio
import re
import smtplib
import threading
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any, LiteralString
from unittest.mock import AsyncMock

import httpx
import psycopg
import pytest
from apps.gateway import main as gateway
from csa.console.auth import build_identity_router
from csa.console.identity import IdentityConfig, IdentityService
from csa.console.mailer import Mailer, OutgoingMail
from csa.console.security import digest
from csa.core import db
from fastapi import FastAPI
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pydantic import SecretStr
from structlog.testing import capture_logs

from tests.conftest import requires_docker, reset_console_identity


def application(
    monkeypatch: pytest.MonkeyPatch,
    *,
    env: str = "ci",
    whatsapp: tuple[str, str] | None = None,
) -> FastAPI:
    synthetic = SimpleNamespace(
        ENV=env,
        HR_ASSISTANT_ENABLED=False,
        VOICE_STT_ENDPOINT=None,
        VOICE_STT_MODEL="synthetic-whisper",
        voice_transcription_key=lambda: None,
        voice_session_config=lambda: None,
        EMAIL_ASSISTANT_ENABLED=False,
        WORKSPACE_ASSISTANT_ENABLED=False,
        whatsapp_enabled=whatsapp is not None,
        WHATSAPP_APP_SECRET=SecretStr(whatsapp[0]) if whatsapp else None,
        WHATSAPP_VERIFY_TOKEN=SecretStr(whatsapp[1]) if whatsapp else None,
        DATABASE_URL="postgresql://csa_app:test@localhost:1/disposable_fixture",
        IDENTITY_DATABASE_URL="postgresql://csa_identity:test@localhost:1/disposable_fixture",
        CONSOLE_ALLOWED_ORIGIN="https://console.example.test",
        CONSOLE_COOKIE_SECURE=True,
        CONSOLE_SESSION_IDLE_SECONDS=1800,
        CONSOLE_SESSION_ABSOLUTE_SECONDS=43200,
        CONSOLE_API_TOKEN=None,
        CONSOLE_MACHINE_CLIENT_IDS=[],
        OTEL_EXPORTER="console",
        OTEL_EXPORTER_OTLP_ENDPOINT=None,
        TEI_ENDPOINT=None,
        KB_EMBED_MODEL_LABEL="synthetic-384",
        KB_UPLOAD_ROOT=None,
        INVITE_MAIL_ENABLED=False,
        INVITE_MAIL_FROM="FourStacks <no-reply@fourstacks.my>",
        SMTP_HOST="smtp-relay.example.test",
        SMTP_PORT=587,
        SMTP_USERNAME=None,
        SMTP_PASSWORD=None,
    )
    monkeypatch.setattr(gateway, "get_settings", lambda: synthetic)
    monkeypatch.setattr(gateway, "configure_logging", lambda **_: None)
    monkeypatch.setattr(gateway, "configure_tracing", lambda **_: None)
    monkeypatch.setattr(gateway, "instrument_fastapi", lambda _: None)
    monkeypatch.setattr(
        gateway, "HrService", lambda *_, **__: SimpleNamespace(check_ready=AsyncMock())
    )
    return gateway.create_app()


@pytest.mark.parametrize(
    "failure", [None, "domain-open", "identity-open", "identity-role", "hr-role"]
)
async def test_identity_pool_is_explicitly_opened_validated_and_closed_on_all_paths(
    monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    events: list[str] = []

    class Pool:
        def __init__(self, name: str) -> None:
            self.name = name
            self.closed = False

        async def open(self, *, wait: bool) -> None:
            assert wait is True
            events.append(f"{self.name}.open")
            if failure == f"{self.name}-open":
                raise RuntimeError("synthetic startup failure")

        async def close(self) -> None:
            events.append(f"{self.name}.close")
            self.closed = True

    domain, identity = Pool("domain"), Pool("identity")

    def factory(dsn: str, **options: Any) -> Pool:
        assert options["autocommit"] is True
        if "csa_identity" in dsn:
            assert options == {"min_size": 1, "max_size": 4, "autocommit": True}
            return identity
        assert options["min_size"] == gateway.GATEWAY_WORKER_MAX_SIZE
        return domain

    class Service:
        def __init__(self, pool: Pool, config: IdentityConfig) -> None:
            assert pool is identity
            assert config.origins == ("https://console.example.test",)
            assert config.secure_cookies is True

        async def check_ready(self) -> None:
            events.append("identity.check")
            if failure == "identity-role":
                raise RuntimeError("synthetic identity role failure")

    monkeypatch.setattr(gateway, "build_pool", factory)
    monkeypatch.setattr(gateway, "IdentityService", Service)
    app = application(monkeypatch)

    class HrDomainService:
        def __init__(self, pool: Pool, *, session_idle_seconds: int):
            assert pool is domain and session_idle_seconds == 1800

        async def check_ready(self) -> None:
            events.append("hr.check")
            if failure == "hr-role":
                raise RuntimeError("synthetic HR role failure")

    monkeypatch.setattr(gateway, "HrService", HrDomainService)
    if failure:
        with pytest.raises(RuntimeError, match="synthetic"):
            async with app.router.lifespan_context(app):
                pytest.fail("startup failure must not yield a running application")
    else:
        async with app.router.lifespan_context(app):
            assert app.state.pool is domain
            assert isinstance(app.state.identity_service, Service)
            assert isinstance(app.state.mailer, Mailer) and app.state.mailer.enabled is False
            assert events == ["domain.open", "identity.open", "identity.check", "hr.check"]
            assert not domain.closed and not identity.closed
    assert domain.closed and identity.closed
    assert events[-2:] == ["identity.close", "domain.close"]


async def test_domain_pool_still_closes_when_identity_shutdown_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    domain = SimpleNamespace(open=AsyncMock(), close=AsyncMock())
    identity = SimpleNamespace(
        open=AsyncMock(), close=AsyncMock(side_effect=RuntimeError("synthetic shutdown failure"))
    )
    monkeypatch.setattr(
        gateway,
        "build_pool",
        lambda dsn, **_: identity if "csa_identity" in dsn else domain,
    )
    monkeypatch.setattr(
        gateway, "IdentityService", lambda *_: SimpleNamespace(check_ready=AsyncMock())
    )
    app = application(monkeypatch)
    with pytest.raises(RuntimeError, match="synthetic shutdown failure"):
        async with app.router.lifespan_context(app):
            pass
    domain.close.assert_awaited_once()
    identity.close.assert_awaited_once()


async def test_readiness_checks_identity_pool_after_domain_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = application(monkeypatch)
    app.state.pool = object()
    identity = SimpleNamespace(check_ready=AsyncMock())
    app.state.identity_service = identity
    hr = SimpleNamespace(check_ready=AsyncMock())
    app.state.hr_service = hr
    domain_check = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(gateway, "check_readiness", domain_check)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://console.example.test"
    ) as client:
        ready = await client.get("/readyz")
        assert ready.status_code == 200 and ready.json()["status"] == "ready"
        identity.check_ready.assert_awaited_once()
        hr.check_ready.assert_awaited_once()
        identity.check_ready.side_effect = RuntimeError("synthetic secret-free identity outage")
        failed = await client.get("/readyz")
        assert failed.status_code == 503
        assert failed.json()["reason"] == "identity database unavailable: RuntimeError"
        identity.check_ready.side_effect = None
        hr.check_ready.side_effect = RuntimeError("synthetic HR outage")
        hr_failed = await client.get("/readyz")
        assert hr_failed.status_code == 503
        assert hr_failed.json()["reason"] == "HR database unavailable: RuntimeError"
        domain_check.return_value = False, "missing migrations: 0270"
        identity.check_ready.reset_mock()
        unavailable = await client.get("/readyz")
        assert unavailable.status_code == 503
        assert "0270" in unavailable.json()["reason"]
        identity.check_ready.assert_not_awaited()


@pytest.mark.parametrize("env", ["ci", "prod"])
async def test_session_routes_are_present_without_machine_token_and_prod_docs_are_disabled(
    monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    app = application(monkeypatch, env=env)
    paths = set(app.openapi()["paths"])
    assert {
        "/console/v1/auth/login",
        "/console/v1/auth/me",
        "/console/v1/auth/logout",
        "/console/v1/invites/accept",
        "/console/v1/admin/clients",
    } <= paths
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://console.example.test"
    ) as client:
        docs = await client.get("/docs")
        schema = await client.get("/openapi.json")
    assert docs.status_code == schema.status_code == (404 if env == "prod" else 200)


async def test_console_validation_errors_never_echo_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from csa.console.identity import IdentityService
    from csa.core.db import build_pool

    app = application(monkeypatch)
    # Validation fails before any database work; use a typed service without opening its pool.
    app.state.identity_service = IdentityService(
        build_pool("postgresql://csa_identity:synthetic@localhost:1/disposable_fixture"),
        IdentityConfig(origins=("https://console.example.test",)),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://console.example.test"
    ) as client:
        secret = "synthetic-password-must-never-appear-in-error"
        response = await client.post(
            "/console/v1/auth/login", json={"email": "invalid", "password": secret}
        )
    assert response.status_code == 422
    assert secret not in response.text
    assert all("input" not in error for error in response.json()["detail"])
    assert response.headers["Cache-Control"] == "no-store"


# Synthetic, DB-backed invitation and reset delivery through the real identity routes.
ORIGIN = "https://console.example.test"
ADMIN_PASSWORD = "synthetic-admin-password-456"
MEMBER_PASSWORD = "synthetic-member-password-789"
SENDER = "FourStacks <no-reply@fourstacks.my>"
LINK = re.compile(r"https://console\.example\.test/invite#token=([A-Za-z0-9_-]+)")


class FakeTransport:
    """Records mail instead of contacting a relay; raises ``failure`` when one is set."""

    def __init__(self) -> None:
        self.sent: list[OutgoingMail] = []
        self.failure: BaseException | None = None

    def send(self, mail: OutgoingMail, sender: str) -> None:
        assert sender == SENDER
        if self.failure is not None:
            raise self.failure
        self.sent.append(mail)


class MailWorld:
    def __init__(self, owner_dsn: str, app: FastAPI, transport: FakeTransport) -> None:
        self.owner_dsn = owner_dsn
        self.app = app
        self.transport = transport
        self.browsers: list[httpx.AsyncClient] = []
        self.platform: httpx.AsyncClient
        self.platform_id = ""
        # hr_tenant has the hr package; plain_tenant does not.
        self.hr_tenant = str(uuid.uuid4())
        self.plain_tenant = str(uuid.uuid4())
        self.owner: httpx.AsyncClient
        self.owner_id = ""
        self.other: httpx.AsyncClient

    async def browser(self) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=ORIGIN)
        self.browsers.append(client)
        assert (await client.get("/console/v1/auth/csrf")).status_code == 200
        return client

    async def mutate(
        self, client: httpx.AsyncClient, path: str, body: Any = None
    ) -> httpx.Response:
        headers = {"Origin": ORIGIN, "X-CSRF-Token": client.cookies["csa_csrf"]}
        return await client.post(path, json=body, headers=headers)

    async def q(self, statement: LiteralString, *params: Any) -> list[dict[str, Any]]:
        async with await psycopg.AsyncConnection.connect(self.owner_dsn, autocommit=True) as conn:
            async with conn.cursor(row_factory=dict_row) as cursor:
                await cursor.execute(statement, params)
                return await cursor.fetchall() if cursor.description else []

    async def invite(
        self,
        client: httpx.AsyncClient,
        *,
        tenant: str | None = None,
        role: str = "tenant_staff",
        path: str = "/console/v1/team/invites",
        display_name: str = "Synthetic Invitee",
        **extra: Any,
    ) -> tuple[httpx.Response, str]:
        email = f"synthetic-{uuid.uuid4()}@example.test"
        body = {
            "client_id": tenant or self.hr_tenant,
            "email": email,
            "display_name": display_name,
            "role": role,
            **extra,
        }
        return await self.mutate(client, path, body), email

    def last_token(self) -> str:
        match = LINK.search(self.transport.sent[-1].text)
        assert match is not None
        return match.group(1)

    async def accept(self, raw: str) -> tuple[httpx.AsyncClient, str]:
        client = await self.browser()
        accepted = await self.mutate(
            client, "/console/v1/invites/accept", {"token": raw, "password": MEMBER_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text
        return client, accepted.json()["user"]["id"]

    async def admin(self, name: str) -> tuple[httpx.AsyncClient, str]:
        response, _ = await self.invite(
            self.platform, role="tenant_admin", path="/console/v1/admin/invites", display_name=name
        )
        assert response.status_code == 201, response.text
        return await self.accept(self.last_token())


@pytest.fixture
async def mail_world(migrated_dsn: str, app_dsn: str) -> AsyncIterator[MailWorld]:
    async with await psycopg.AsyncConnection.connect(migrated_dsn, autocommit=True) as owner:
        await reset_console_identity(owner)
        await owner.execute(
            sql.SQL("ALTER ROLE csa_identity LOGIN PASSWORD {}").format(
                sql.Literal("synthetic_mail_fixture_pw")
            )
        )
    identity_dsn = make_conninfo(
        migrated_dsn, user="csa_identity", password="synthetic_mail_fixture_pw"
    )
    identity_pool = db.build_pool(identity_dsn, min_size=1, max_size=4, autocommit=True)
    domain_pool = db.build_pool(app_dsn, min_size=1, max_size=4, autocommit=True)
    await identity_pool.open(wait=True)
    await domain_pool.open(wait=True)
    service = IdentityService(identity_pool, IdentityConfig(origins=(ORIGIN,)))
    app = FastAPI()
    app.state.identity_service = service
    app.state.pool = domain_pool
    transport = FakeTransport()
    app.state.mailer = Mailer(transport, SENDER)
    app.include_router(build_identity_router())
    world = MailWorld(migrated_dsn, app, transport)
    try:
        for tenant, name, zone in (
            (world.hr_tenant, "Kedai Synthetic HR", "Asia/Jakarta"),
            (world.plain_tenant, "Kedai Synthetic Plain", "Asia/Kuala_Lumpur"),
        ):
            await world.q(
                "INSERT INTO clients(id,slug,name,platform_version,timezone) "
                "VALUES (%s,%s,%s,'test',%s)",
                tenant,
                f"mail-{tenant}",
                name,
                zone,
            )
        await world.q(
            "INSERT INTO console_service_packages (client_id, package, status, activated_at, "
            "reference) VALUES (%s, 'hr', 'active', now(), 'test-fixture')",
            world.hr_tenant,
        )
        world.platform_id = str(
            await service.bootstrap(
                "synthetic-admin@example.test", ADMIN_PASSWORD, "Synthetic Admin"
            )
        )
        await world.q(
            "UPDATE console_users SET must_change_password=false WHERE id=%s", world.platform_id
        )
        world.platform = await world.browser()
        login = await world.mutate(
            world.platform,
            "/console/v1/auth/login",
            {"email": "synthetic-admin@example.test", "password": ADMIN_PASSWORD},
        )
        assert login.status_code == 200, login.text
        world.owner, world.owner_id = await world.admin("Synthetic Owner")
        world.other, _ = await world.admin("Synthetic Other")
        await world.q(
            "INSERT INTO hr_client_owners(client_id,user_id,approved_by) VALUES (%s,%s,%s)",
            world.hr_tenant,
            world.owner_id,
            world.platform_id,
        )
        transport.sent.clear()
        yield world
    finally:
        await asyncio.gather(*(client.aclose() for client in world.browsers))
        await identity_pool.close()
        await domain_pool.close()


async def audits(world: MailWorld, action: str) -> list[dict[str, Any]]:
    return await world.q(
        "SELECT target_id, details FROM console_audit WHERE action=%s ORDER BY created_at, id",
        action,
    )


@requires_docker
async def test_invite_emails_and_hides_the_token(mail_world: MailWorld) -> None:
    response, email = await mail_world.invite(mail_world.owner)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["delivery"] == "email" and body["token"] is None
    [mail] = mail_world.transport.sent
    assert mail.to == email
    assert mail.subject == "You're invited to Kedai Synthetic HR on FourStacks"
    assert "/invite#token=" in mail.text and "/invite#token=" in mail.html
    assert "Kedai Synthetic HR" in mail.text and "Synthetic Invitee" in mail.text
    assert "Synthetic Owner has invited you to join Kedai Synthetic HR" in mail.text
    raw = mail_world.last_token()
    [token] = await mail_world.q(
        "SELECT t.token_hash FROM console_tokens t JOIN console_users u ON u.id=t.user_id "
        "WHERE u.email=%s",
        email,
    )
    assert token["token_hash"] == digest(raw)
    # The emailed link is the working credential.
    member, _ = await mail_world.accept(raw)
    assert (await member.get("/console/v1/auth/me")).status_code == 200


@requires_docker
async def test_mail_failure_falls_back_to_manual_without_leaking(
    mail_world: MailWorld, caplog: pytest.LogCaptureFixture
) -> None:
    mail_world.transport.failure = TimeoutError("synthetic relay timeout")
    caplog.set_level("DEBUG")
    with capture_logs() as logs:
        response, email = await mail_world.invite(mail_world.owner)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["delivery"] == "manual" and body["token"]
    users = await mail_world.q("SELECT id FROM console_users WHERE email=%s", email)
    assert len(users) == 1
    tokens = await mail_world.q("SELECT id FROM console_tokens WHERE user_id=%s", users[0]["id"])
    assert len(tokens) == 1 and str(tokens[0]["id"]) == body["id"]
    captured = repr(logs) + caplog.text
    assert "smtp_timeout" in captured
    assert email not in captured and "#token=" not in captured and body["token"] not in captured
    assert mail_world.transport.sent == []


@requires_docker
async def test_renew_and_reset_email(mail_world: MailWorld) -> None:
    transport = mail_world.transport
    transport.failure = smtplib.SMTPAuthenticationError(535, b"synthetic")
    response, email = await mail_world.invite(mail_world.owner)
    assert response.json()["delivery"] == "manual"
    token_id = response.json()["id"]
    transport.failure = None
    renewed = await mail_world.mutate(
        mail_world.other, f"/console/v1/admin/invites/{token_id}/renew"
    )
    assert renewed.status_code == 201, renewed.text
    assert renewed.json()["delivery"] == "email" and renewed.json()["token"] is None
    [mail] = transport.sent
    assert mail.to == email and "Kedai Synthetic HR" in mail.text
    transport.failure = TimeoutError("synthetic relay timeout")
    again = await mail_world.mutate(
        mail_world.owner, f"/console/v1/admin/invites/{renewed.json()['id']}/renew"
    )
    assert again.status_code == 201, again.text
    assert again.json()["delivery"] == "manual" and again.json()["token"]
    _, member_id = await mail_world.accept(again.json()["token"])
    transport.failure = None
    reset = await mail_world.mutate(
        mail_world.platform, f"/console/v1/admin/users/{member_id}/reset"
    )
    assert reset.status_code == 201, reset.text
    assert reset.json()["delivery"] == "email" and reset.json()["token"] is None
    assert transport.sent[-1].to == email
    assert transport.sent[-1].subject == "Reset your FourStacks password"
    assert "/invite#token=" in transport.sent[-1].text
    transport.failure = TimeoutError("synthetic relay timeout")
    manual = await mail_world.mutate(
        mail_world.platform, f"/console/v1/admin/users/{member_id}/reset"
    )
    assert manual.status_code == 201, manual.text
    assert manual.json()["delivery"] == "manual" and manual.json()["token"]
    # Tenant admins still cannot reset passwords, and nothing is mailed when they try.
    sent = len(transport.sent)
    denied = await mail_world.mutate(mail_world.owner, f"/console/v1/admin/users/{member_id}/reset")
    assert denied.status_code == 403 and len(transport.sent) == sent


@requires_docker
async def test_owner_inviter_may_choose_manager_through_the_route(mail_world: MailWorld) -> None:
    response, email = await mail_world.invite(mail_world.owner, hr_preset="manager")
    assert response.status_code == 201, response.text
    [token] = await mail_world.q(
        "SELECT t.hr_preset, t.hr_preset_chosen_by FROM console_tokens t "
        "JOIN console_users u ON u.id=t.user_id WHERE u.email=%s",
        email,
    )
    assert token["hr_preset"] == "manager"
    assert str(token["hr_preset_chosen_by"]) == mail_world.owner_id
    denied, other_email = await mail_world.invite(mail_world.other, hr_preset="manager")
    assert denied.status_code == 403 and denied.json()["detail"] == "hr_preset_requires_hr_owner"
    assert await mail_world.q("SELECT 1 FROM console_users WHERE email=%s", other_email) == []
    staff, _ = await mail_world.invite(mail_world.other, hr_preset="staff")
    assert staff.status_code == 201, staff.text


@requires_docker
async def test_platform_admin_may_choose_only_staff_through_admin_invites(
    mail_world: MailWorld,
) -> None:
    path = "/console/v1/admin/invites"
    denied, _ = await mail_world.invite(mail_world.platform, path=path, hr_preset="manager")
    assert denied.status_code == 403 and denied.json()["detail"] == "hr_preset_requires_hr_owner"
    staff, _ = await mail_world.invite(mail_world.platform, path=path, hr_preset="staff")
    assert staff.status_code == 201, staff.text
    # Even when a platform admin is also the tenant's HR owner, the platform session never
    # carries the owner's right to grant non-Staff presets.
    await mail_world.q(
        "INSERT INTO console_memberships(user_id,client_id,role) VALUES (%s,%s,'tenant_admin')",
        mail_world.platform_id,
        mail_world.hr_tenant,
    )
    await mail_world.q(
        "UPDATE hr_client_owners SET user_id=%s WHERE client_id=%s",
        mail_world.platform_id,
        mail_world.hr_tenant,
    )
    for route in (path, "/console/v1/team/invites"):
        again, _ = await mail_world.invite(mail_world.platform, path=route, hr_preset="manager")
        assert again.status_code == 403 and again.json()["detail"] == "hr_preset_requires_hr_owner"


@requires_docker
async def test_preset_refused_without_hr_package(mail_world: MailWorld) -> None:
    path = "/console/v1/admin/invites"
    for preset in ("staff", "manager"):
        refused, email = await mail_world.invite(
            mail_world.platform, tenant=mail_world.plain_tenant, path=path, hr_preset=preset
        )
        assert refused.status_code == 422 and refused.json()["detail"] == "hr_not_available"
        assert await mail_world.q("SELECT 1 FROM console_users WHERE email=%s", email) == []
    assert mail_world.transport.sent == []
    plain, _ = await mail_world.invite(
        mail_world.platform, tenant=mail_world.plain_tenant, path=path
    )
    assert plain.status_code == 201, plain.text
    assert "Kedai Synthetic Plain" in mail_world.transport.sent[-1].text
    # A tenant admin cannot probe another tenant's packages: authorisation comes first.
    probe, _ = await mail_world.invite(
        mail_world.owner, tenant=mail_world.plain_tenant, hr_preset="staff"
    )
    assert probe.status_code == 403 and probe.json()["detail"] == "tenant administrator required"


@requires_docker
async def test_audit_records_delivery(mail_world: MailWorld) -> None:
    transport = mail_world.transport
    emailed, _ = await mail_world.invite(mail_world.owner)
    transport.failure = TimeoutError("synthetic relay timeout")
    manual, _ = await mail_world.invite(mail_world.owner)
    renewed = await mail_world.mutate(
        mail_world.owner, f"/console/v1/admin/invites/{manual.json()['id']}/renew"
    )
    transport.failure = None
    _, member_id = await mail_world.accept(renewed.json()["token"])
    reset = await mail_world.mutate(
        mail_world.platform, f"/console/v1/admin/users/{member_id}/reset"
    )
    assert reset.json()["delivery"] == "email"
    # The mutation's own audit rows are unchanged and committed with it.
    invited = await audits(mail_world, "identity.invited")
    assert [row["details"] for row in invited[-2:]] == [{"role": "tenant_staff"}] * 2
    [renewal] = await audits(mail_world, "identity.invitation_renewed")
    assert renewal["details"] == {"previous_token_id": manual.json()["id"]}
    assert renewal["target_id"] == renewed.json()["id"]
    [reset_row] = await audits(mail_world, "identity.reset_issued")
    assert reset_row["details"] == {} and reset_row["target_id"] == member_id
    # Each issued token gets one follow-up row with the outcome, and nothing else.
    outcomes = {
        row["target_id"]: row["details"] for row in await audits(mail_world, "identity.delivery")
    }
    for issued, kind, delivery in (
        (emailed, "invite", "email"),
        (manual, "invite", "manual"),
        (renewed, "invite", "manual"),
        (reset, "reset", "email"),
    ):
        token_id = issued.json()["id"]
        assert outcomes[token_id] == {"token_id": token_id, "kind": kind, "delivery": delivery}
    everything = repr(await mail_world.q("SELECT details FROM console_audit"))
    assert "#token=" not in everything and renewed.json()["token"] not in everything
    assert "@example.test" not in repr(list(outcomes.values()))
    assert emailed.json()["token"] is None


@requires_docker
async def test_failed_delivery_audit_still_returns_the_invitation(
    mail_world: MailWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = mail_world.app.state.identity_service
    original = service._audit

    async def failing(conn: Any, actor_id: Any, action: str, *args: Any, **details: Any) -> None:
        if action == "identity.delivery":
            raise psycopg.OperationalError("synthetic audit outage")
        await original(conn, actor_id, action, *args, **details)

    monkeypatch.setattr(service, "_audit", failing)
    with capture_logs() as logs:
        response, email = await mail_world.invite(mail_world.owner)
    assert response.status_code == 201, response.text
    assert response.json()["delivery"] == "email" and response.json()["token"] is None
    assert {
        "event": "console_delivery_audit_failed",
        "category": "audit_write_failed",
        "log_level": "warning",
    } in logs
    assert email not in repr(logs)
    [user] = await mail_world.q("SELECT id FROM console_users WHERE email=%s", email)
    [invited] = await mail_world.q(
        "SELECT 1 FROM console_audit WHERE action='identity.invited' AND target_id=%s",
        str(user["id"]),
    )
    assert invited


@requires_docker
async def test_slow_relay_falls_back_to_manual(mail_world: MailWorld) -> None:
    release = threading.Event()

    class SlowTransport:
        def send(self, mail: OutgoingMail, sender: str) -> None:
            release.wait(5)

    mail_world.app.state.mailer = Mailer(SlowTransport(), SENDER, limit=0.05)
    try:
        with capture_logs() as logs:
            response, email = await mail_world.invite(mail_world.owner)
    finally:
        release.set()
    assert response.status_code == 201, response.text
    assert response.json()["delivery"] == "manual" and response.json()["token"]
    assert {
        "event": "console_mail_not_sent",
        "category": "smtp_timeout",
        "log_level": "warning",
    } in logs
    assert len(await mail_world.q("SELECT 1 FROM console_users WHERE email=%s", email)) == 1
