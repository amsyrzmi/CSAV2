"""Explicit identity-pool startup, shutdown, readiness and API exposure."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from apps.gateway import main as gateway
from csa.console.identity import IdentityConfig
from fastapi import FastAPI


def application(monkeypatch: pytest.MonkeyPatch, *, env: str = "ci") -> FastAPI:
    synthetic = SimpleNamespace(
        ENV=env,
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
    )
    monkeypatch.setattr(gateway, "get_settings", lambda: synthetic)
    monkeypatch.setattr(gateway, "configure_logging", lambda **_: None)
    monkeypatch.setattr(gateway, "configure_tracing", lambda **_: None)
    monkeypatch.setattr(gateway, "instrument_fastapi", lambda _: None)
    return gateway.create_app()


@pytest.mark.parametrize("failure", [None, "domain-open", "identity-open", "identity-role"])
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
    if failure:
        with pytest.raises(RuntimeError, match="synthetic"):
            async with app.router.lifespan_context(app):
                pytest.fail("startup failure must not yield a running application")
    else:
        async with app.router.lifespan_context(app):
            assert app.state.pool is domain
            assert isinstance(app.state.identity_service, Service)
            assert events == ["domain.open", "identity.open", "identity.check"]
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
    domain_check = AsyncMock(return_value=(True, "ok"))
    monkeypatch.setattr(gateway, "check_readiness", domain_check)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://console.example.test"
    ) as client:
        ready = await client.get("/readyz")
        assert ready.status_code == 200 and ready.json()["status"] == "ready"
        identity.check_ready.assert_awaited_once()
        identity.check_ready.side_effect = RuntimeError("synthetic secret-free identity outage")
        failed = await client.get("/readyz")
        assert failed.status_code == 503
        assert failed.json()["reason"] == "identity database unavailable: RuntimeError"
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
