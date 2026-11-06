"""Console connect, callback and disconnect for TikTok Shop on synthetic tenants; never TikTok.

Every provider call goes through httpx.MockTransport (``mock_tiktok``) or a fake verifier.
"""

import dataclasses
import hashlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.agents.ecommerce.apps import register_app, transition_app
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.authorization_verifier import TikTokFirstConnectVerifier
from csa.console import tiktok_connect
from csa.console.tiktok_connect import AUTHORIZE_URL, REASONS, TikTokConnect, build_tiktok_connect
from csa.core.tenancy import tx
from csa.jobs.clock import FakeClock
from csa.marketplace.disconnect import disconnect_store
from csa.marketplace.jobs import enqueue_job
from csa.marketplace.retention import run_marketplace_catch_up
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_identity_config import configured
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_ecommerce_connections import FIRST_SECRETS, mock_tiktok
from tests.test_ecommerce_storage import encrypt

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

CONNECT = "/console/v1/business/stores/tiktok/connect"
CALLBACK = "/console/v1/business/stores/tiktok/callback"
CODE = "SYNTHETIC-AUTHORIZATION-CODE"
APP_SECRET = "synthetic-connect-app-secret"
STATE = re.compile(r"[0-9a-f]{32}[0-9a-f]{32}[A-Za-z0-9_-]{43}")


class Recorder:
    """Test doubles for the Task 12/14 functions the routes call through TikTokConnect."""

    def __init__(self) -> None:
        self.jobs: list[tuple[UUID, UUID, str]] = []
        self.disconnects: list[tuple[UUID, UUID, str]] = []

    async def enqueue_job(self, conn: Any, *, client_id: UUID, store_id: UUID, kind: str) -> bool:
        assert conn is not None
        self.jobs.append((client_id, store_id, kind))
        return True

    async def disconnect_store(
        self, pool: Any, client_id: UUID, store_id: UUID, *, reason: str
    ) -> None:
        assert pool is not None
        self.disconnects.append((client_id, store_id, reason))


@pytest.fixture
async def world(sessions: SessionWorld) -> SessionWorld:
    """Both tenants hold orders_products and marketplaces, with an active synthetic app."""
    for tenant in sessions.tenants:
        await activate(sessions, tenant, "orders_products")
        # Inserted directly, as 0450 backfilled it, so the fixture needs no
        # request/activate round trip.
        await sessions.q(
            "INSERT INTO console_service_packages(client_id,package,status,activated_at,reference)"
            " VALUES (%s,'marketplaces','active',now(),'backfill-0450')",
            tenant,
        )
    app_id = f"tt_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(sessions.owner_dsn) as conn:
        state = await register_app(conn, app_id)
        await transition_app(conn, app_id=app_id, expected_revision=state.revision, state="active")
    settings = configured(
        TIKTOK_APP_KEY="synthetic-connect-app-key",
        TIKTOK_APP_SECRET=APP_SECRET,
        TIKTOK_SERVICE_ID="SYNTHETIC-SERVICE-ID",
        TIKTOK_APP_ID=app_id,
    )
    recorder = Recorder()
    sessions.app.state.tiktok_connect = dataclasses.replace(
        build_tiktok_connect(settings, sessions.pool),
        encrypt=encrypt,
        enqueue_job=recorder.enqueue_job,
    )
    sessions.app.state.recorder = recorder
    return sessions


def wiring(w: SessionWorld) -> TikTokConnect:
    value = w.app.state.tiktok_connect
    assert isinstance(value, TikTokConnect)
    return value


def rewire(w: SessionWorld, **changes: Any) -> None:
    w.app.state.tiktok_connect = dataclasses.replace(wiring(w), **changes)


def recorder(w: SessionWorld) -> Recorder:
    value = w.app.state.recorder
    assert isinstance(value, Recorder)
    return value


async def connect(
    w: SessionWorld,
    client: httpx.AsyncClient,
    body: dict[str, Any] | None = None,
    *,
    tenant: str | None = None,
) -> httpx.Response:
    path = CONNECT + (f"?client_id={tenant}" if tenant else "")
    return await w.mutate(client, path, body if body is not None else {})


def state_of(response: httpx.Response) -> str:
    assert response.status_code == 200, response.text
    query = parse_qs(urlsplit(response.json()["authorization_url"]).query)
    return query["state"][0]


async def callback(client: httpx.AsyncClient, **params: str) -> httpx.Response:
    return await client.get(CALLBACK, params=params)


def outcome(response: httpx.Response) -> tuple[str, dict[str, str]]:
    assert response.status_code == 303, response.text
    assert response.headers["cache-control"] == "no-store"
    location = urlsplit(response.headers["location"])
    assert not location.scheme and not location.netloc
    query = {key: values[0] for key, values in parse_qs(location.query).items()}
    assert all(len(values) == 1 for values in parse_qs(location.query).values())
    return location.path, query


async def attempts(w: SessionWorld, tenant: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id,status,state_hash,context::text AS context FROM ecommerce_authorization_attempts"
        " WHERE client_id=%s ORDER BY created_at",
        tenant,
    )


async def stores(w: SessionWorld, tenant: str) -> list[dict[str, Any]]:
    return await w.q(
        "SELECT id,shop_id,credential_generation,access_token,disconnected_at"
        " FROM channel_credentials WHERE client_id=%s AND channel='tiktok_shop' ORDER BY id",
        tenant,
    )


async def admin_of(w: SessionWorld, tenant: str | None = None) -> httpx.AsyncClient:
    client, _ = await w.member(role="tenant_admin", tenant=tenant)
    return client


async def test_connect_denied_for_staff_and_other_tenant_admin(world: SessionWorld) -> None:
    w = world
    staff, _ = await w.member(role="tenant_staff")
    reader, _ = await w.member(role="read_only")
    foreign_admin = await admin_of(w, w.tenants[1])
    store = uuid4()
    for client in (staff, reader):
        assert (await connect(w, client)).status_code == 403
        assert (
            await w.mutate(client, f"/console/v1/business/stores/{store}/disconnect")
        ).status_code == 403
        # The callback is refused by role before the state is even read.
        refused = await callback(client, code=CODE, state="0" * 107)
        assert refused.status_code == 403
    denied = await connect(w, foreign_admin, tenant=w.tenants[0])
    assert denied.status_code == 403
    assert await attempts(w, w.tenants[0]) == []
    assert await stores(w, w.tenants[0]) == []


async def test_connect_requires_the_marketplaces_package(world: SessionWorld) -> None:
    w = world
    await w.q(
        "DELETE FROM console_service_packages WHERE client_id=%s AND package='marketplaces'",
        w.tenants[0],
    )
    admin = await admin_of(w)
    response = await connect(w, admin)
    assert response.status_code == 403 and response.json()["detail"] == "package_inactive"
    assert await stores(w, w.tenants[0]) == []
    rewire(w, disconnect_store=recorder(w).disconnect_store)
    refused = await w.mutate(admin, f"/console/v1/business/stores/{uuid4()}/disconnect")
    assert refused.status_code == 403 and refused.json()["detail"] == "package_inactive"
    assert recorder(w).disconnects == []


@pytest.mark.parametrize("role", ["tenant_admin", "platform_admin"])
async def test_callback_refuses_a_client_without_the_marketplaces_package(
    world: SessionWorld,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    role: str,
) -> None:
    """The callback's own package check stands in for package_gate (its tenant is the state's)."""
    w = world
    tenant = w.tenants[0]
    seen = mock_tiktok(monkeypatch, [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-FIRST-CIPHER")])
    if role == "platform_admin":
        client, query, root, extra = w.admin, tenant, "/business/stores", {"client_id": tenant}
    else:
        client, query, root, extra = await admin_of(w), None, "/portal/business/stores", {}
    state = state_of(await connect(w, client, tenant=query))
    await w.q(
        "DELETE FROM console_service_packages WHERE client_id=%s AND package='marketplaces'",
        tenant,
    )
    with capture_logs() as logs:
        response = await callback(client, code=CODE, state=state)
    assert outcome(response) == (root, {**extra, "tiktok": "failed", "reason": "connect_failed"})
    assert [a["status"] for a in await attempts(w, tenant)] == ["pending"]
    (store,) = await stores(w, tenant)
    assert store["shop_id"] is None and store["access_token"] is None
    assert seen == [] and recorder(w).jobs == []
    assert [
        (e["log_level"], e.get("code")) for e in logs if e["event"] == "tiktok_connect_failed"
    ] == [("warning", "package_inactive")]


async def test_connect_returns_authorize_url_with_service_id_and_state_and_stores_only_hash(
    world: SessionWorld,
) -> None:
    w = world
    admin = await admin_of(w)
    response = await connect(w, admin)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    url = urlsplit(response.json()["authorization_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == AUTHORIZE_URL
    assert AUTHORIZE_URL == "https://services.tiktokshop.com/open/authorize"
    query = parse_qs(url.query)
    assert set(query) == {"service_id", "state"}
    assert query["service_id"] == ["SYNTHETIC-SERVICE-ID"]
    state = query["state"][0]
    assert STATE.fullmatch(state)
    (attempt,) = await attempts(w, w.tenants[0])
    assert state[:32] == UUID(w.tenants[0]).hex
    assert state[32:64] == attempt["id"].hex
    secret = state[64:]
    assert attempt["status"] == "pending"
    assert attempt["state_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in attempt["context"] and state not in attempt["context"]
    (store,) = await stores(w, w.tenants[0])
    assert store["shop_id"] is None and store["access_token"] is None
    assert str(store["id"]) in attempt["context"]


async def test_connect_reuses_the_pending_store(world: SessionWorld) -> None:
    w = world
    admin = await admin_of(w)
    first = state_of(await connect(w, admin))
    second = state_of(await connect(w, admin))
    assert first != second
    (store,) = await stores(w, w.tenants[0])
    both = await attempts(w, w.tenants[0])
    assert len(both) == 2 and all(str(store["id"]) in a["context"] for a in both)
    # Another tenant's pending store is never reused.
    other = await admin_of(w, w.tenants[1])
    state_of(await connect(w, other))
    (foreign,) = await stores(w, w.tenants[1])
    assert foreign["id"] != store["id"]


async def test_connect_503_when_tiktok_off(world: SessionWorld) -> None:
    w = world
    admin = await admin_of(w)
    state = state_of(await connect(w, admin))
    before = await attempts(w, w.tenants[0])
    for off in (
        lambda: rewire(w, enabled=False),
        lambda: rewire(w, service_id=None),
        lambda: delattr(w.app.state, "tiktok_connect"),
    ):
        saved = wiring(w)
        off()
        response = await connect(w, admin)
        assert response.status_code == 503 and response.json()["detail"] == "tiktok_unavailable"
        assert outcome(await callback(admin, code=CODE, state=state)) == (
            "/portal/business/stores",
            {"tiktok": "failed", "reason": "tiktok_unavailable"},
        )
        w.app.state.tiktok_connect = saved
    assert await attempts(w, w.tenants[0]) == before
    assert len(await stores(w, w.tenants[0])) == 1


async def test_connect_503_when_the_app_row_is_disabled(world: SessionWorld) -> None:
    w = world
    admin = await admin_of(w)
    app_id = wiring(w).app_id
    async with await psycopg.AsyncConnection.connect(w.owner_dsn) as conn:
        await transition_app(conn, app_id=app_id, expected_revision=2, state="disabled")
    response = await connect(w, admin)
    assert response.status_code == 503 and response.json()["detail"] == "tiktok_unavailable"


@pytest.mark.parametrize("role", ["tenant_admin", "platform_admin"])
async def test_callback_first_connect_redirects_connected_for_company_user_and_platform_admin(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch, role: str
) -> None:
    w = world
    tenant = w.tenants[0]
    if role == "platform_admin":
        client, query = w.admin, tenant
    else:
        client, query = await admin_of(w), None
    shop_id = f"SYNTH-SHOP-{uuid4()}"
    seen = mock_tiktok(monkeypatch, [(shop_id, "SYNTHETIC-FIRST-CIPHER")])
    state = state_of(await connect(w, client, tenant=query))
    response = await callback(client, code=CODE, state=state)
    if role == "platform_admin":
        expected = ("/business/stores", {"client_id": tenant, "tiktok": "connected"})
    else:
        expected = ("/portal/business/stores", {"tiktok": "connected"})
    assert outcome(response) == expected
    assert seen == ["exchange", "shops"]
    (store,) = await stores(w, tenant)
    assert store["shop_id"] == shop_id and store["credential_generation"] == 2
    assert store["access_token"].startswith("v1:")
    (attempt,) = await attempts(w, tenant)
    assert attempt["status"] == "completed"
    assert recorder(w).jobs == [
        (UUID(tenant), store["id"], "backfill_orders"),
        (UUID(tenant), store["id"], "backfill_products"),
    ]


async def test_reconnect_with_store_id_completes_on_the_same_store(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    tenant = w.tenants[0]
    admin = await admin_of(w)
    shop_id = f"SYNTH-SHOP-{uuid4()}"
    mock_tiktok(monkeypatch, [(shop_id, "SYNTHETIC-FIRST-CIPHER")])
    assert outcome(await callback(admin, code=CODE, state=state_of(await connect(w, admin))))[
        1
    ] == {"tiktok": "connected"}
    (store,) = await stores(w, tenant)
    # A disconnected store is reconnected on its own row through store_id.
    await w.q("UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", store["id"])
    state = state_of(await connect(w, admin, {"store_id": str(store["id"])}))
    assert outcome(await callback(admin, code=CODE, state=state))[1] == {"tiktok": "connected"}
    (after,) = await stores(w, tenant)
    assert after["id"] == store["id"] and after["shop_id"] == shop_id
    assert after["disconnected_at"] is None
    assert after["credential_generation"] == store["credential_generation"] + 2
    assert [job[2] for job in recorder(w).jobs] == ["backfill_orders", "backfill_products"] * 2
    # Another tenant's store, or a store that does not exist, is not found.
    foreign = await admin_of(w, w.tenants[1])
    for store_id in (store["id"], uuid4()):
        missing = await connect(w, foreign, {"store_id": str(store_id)})
        assert missing.status_code == 404 and missing.json()["detail"] == "store_not_found"
    assert await attempts(w, w.tenants[1]) == []


async def test_callback_without_session_is_refused(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    seen = mock_tiktok(monkeypatch, [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-FIRST-CIPHER")])
    admin = await admin_of(w)
    state = state_of(await connect(w, admin))
    anonymous = await w.browser()
    response = await callback(anonymous, code=CODE, state=state)
    assert response.status_code == 401
    assert "location" not in response.headers
    assert [a["status"] for a in await attempts(w, w.tenants[0])] == ["pending"]
    assert seen == []


async def test_callback_foreign_client_state_redirects_state_invalid_and_changes_nothing(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    shop_id = f"SYNTH-SHOP-{uuid4()}"
    seen = mock_tiktok(monkeypatch, [(shop_id, "SYNTHETIC-FIRST-CIPHER")])
    owner = await admin_of(w)
    state = state_of(await connect(w, owner))
    before = (await attempts(w, w.tenants[0]), await stores(w, w.tenants[0]))
    foreign = await admin_of(w, w.tenants[1])
    assert outcome(await callback(foreign, code=CODE, state=state)) == (
        "/portal/business/stores",
        {"tiktok": "failed", "reason": "state_invalid"},
    )
    assert (await attempts(w, w.tenants[0]), await stores(w, w.tenants[0])) == before
    assert seen == [] and recorder(w).jobs == []
    # The attempt was untouched, so its own Admin can still finish it.
    assert outcome(await callback(owner, code=CODE, state=state))[1] == {"tiktok": "connected"}


async def test_callback_replayed_state_redirects_state_invalid(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    seen = mock_tiktok(monkeypatch, [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-FIRST-CIPHER")])
    admin = await admin_of(w)
    state = state_of(await connect(w, admin))
    assert outcome(await callback(admin, code=CODE, state=state))[1] == {"tiktok": "connected"}
    assert outcome(await callback(admin, code=CODE, state=state))[1] == {
        "tiktok": "failed",
        "reason": "state_invalid",
    }
    assert seen == ["exchange", "shops"]
    # An expired attempt reads the same, and is never sent to TikTok.
    (store,) = await stores(w, w.tenants[0])
    other = state_of(await connect(w, admin, {"store_id": str(store["id"])}))
    later = datetime.now(UTC) + timedelta(minutes=11)
    monkeypatch.setattr(tiktok_connect, "_now", lambda: later)
    assert outcome(await callback(admin, code=CODE, state=other))[1]["reason"] == "state_invalid"
    assert seen == ["exchange", "shops"]
    assert [a["status"] for a in await attempts(w, w.tenants[0])] == ["completed", "pending"]


MANGLED = {
    "short": lambda s: s[:-1],
    "long": lambda s: s + "A",
    "upper_client": lambda s: s[:31].upper() + s[31:],
    "non_hex": lambda s: "g" + s[1:],
    "bad_secret": lambda s: s[:64] + "!" + s[65:],
    "upper_attempt": lambda s: s[:32] + s[32:64].upper() + s[64:],
    "secret_only": lambda s: s[64:],
    "empty": lambda s: "",
}


async def test_callback_malformed_state_is_refused_before_the_database(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One fixture, every malformation (the session fixture costs ~17 s per test)."""
    w = world
    admin = await admin_of(w)
    state = state_of(await connect(w, admin))

    async def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("a malformed state reached the database")

    monkeypatch.setattr(tiktok_connect, "require_package", forbidden)
    monkeypatch.setattr(tiktok_connect, "pending_attempt_kind", forbidden)
    for name, mangle in MANGLED.items():
        response = await callback(admin, code=CODE, state=mangle(state))
        assert outcome(response)[1] == {"tiktok": "failed", "reason": "state_invalid"}, name
    assert [a["status"] for a in await attempts(w, w.tenants[0])] == ["pending"]


async def test_callback_duplicate_or_missing_parameters_are_state_invalid(
    world: SessionWorld,
) -> None:
    w = world
    admin = await admin_of(w)
    state = state_of(await connect(w, admin))
    queries: list[list[tuple[str, str | int | float | bool | None]]] = [
        [("code", CODE), ("state", state), ("state", state)],
        [("code", CODE), ("code", CODE), ("state", state)],
        [("state", state)],
        [("code", CODE)],
        [("code", ""), ("state", state)],
        [("code", "x" * 2049), ("state", state)],
    ]
    for query in queries:
        response = await admin.get(CALLBACK, params=query)
        assert outcome(response)[1] == {"tiktok": "failed", "reason": "state_invalid"}, query
    assert [a["status"] for a in await attempts(w, w.tenants[0])] == ["pending"]


async def test_callback_provider_error_param_redirects_provider_denied(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    seen = mock_tiktok(monkeypatch, [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-FIRST-CIPHER")])
    state = state_of(await connect(w, w.admin, tenant=w.tenants[0]))
    for params in ({"error": "access_denied", "state": state}, {"error": "x", "code": CODE}):
        assert outcome(await callback(w.admin, **params))[1] == {
            **({"client_id": w.tenants[0]} if "state" in params else {}),
            "tiktok": "failed",
            "reason": "provider_denied",
        }
    assert [a["status"] for a in await attempts(w, w.tenants[0])] == ["pending"]
    assert seen == []


VERIFIER_CODES = {
    "multiple_shops": "multiple_shops",
    "no_authorized_shop": "no_authorized_shop",
    "shop_in_use": "shop_in_use",
    "insufficient_scope": "insufficient_scope",
    # A reconnect that approves a different shop surfaces as this code (Task 5 ruling).
    "authorized_shop_verification_failed": "connect_failed",
    "expired_exchange_token": "connect_failed",
    "token_exchange_http_rejected": "connect_failed",
    "token_exchange_timeout": "connect_failed",
    "invalid_token_response": "connect_failed",
    "binding_mismatch": "connect_failed",
    "invalid_clock": "connect_failed",
    "app_binding_unavailable": "tiktok_unavailable",
    "app_revision_unavailable": "tiktok_unavailable",
    "authorization_attempt_unavailable": "state_invalid",
    "expired_authorization_attempt": "state_invalid",
    "credential_changed": "state_invalid",
    "a_code_nobody_mapped": "connect_failed",
}


async def test_each_failure_reason_is_a_listed_safe_code(world: SessionWorld) -> None:
    """Every verifier/completion code, one fixture; each code gets a fresh attempt."""
    w = world
    assert "shop_mismatch" not in REASONS
    admin = await admin_of(w)
    for code, reason in sorted(VERIFIER_CODES.items()):
        assert reason in REASONS
        calls: list[str] = []

        async def refuse(
            context: Any, authorization_code: Any, code: str = code, calls: list[str] = calls
        ) -> Any:
            calls.append(code)
            raise TikTokAuthorizationError(code)

        rewire(w, verify_first=refuse)
        state = state_of(await connect(w, admin))
        assert outcome(await callback(admin, code=CODE, state=state)) == (
            "/portal/business/stores",
            {"tiktok": "failed", "reason": reason},
        ), code
        assert calls == [code]
        (store,) = await stores(w, w.tenants[0])
        assert store["shop_id"] is None and store["access_token"] is None
    assert recorder(w).jobs == []
    assert {a["status"] for a in await attempts(w, w.tenants[0])} == {"consumed"}


async def test_callback_never_echoes_code_state_or_exception_text(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    w = world
    caplog.set_level(logging.DEBUG)
    leak = "SYNTHETIC-EXCEPTION-TEXT-9f2c"

    async def explode(context: Any, authorization_code: Any) -> Any:
        raise RuntimeError(f"{leak} {authorization_code.get_secret_value()}")

    admin = await admin_of(w)
    shop_id = f"SYNTH-SHOP-{uuid4()}"
    mock_tiktok(monkeypatch, [(shop_id, "SYNTHETIC-FIRST-CIPHER")])
    with capture_logs() as logs:
        rewire(w, verify_first=explode)
        failed_state = state_of(await connect(w, admin))
        failed = await callback(admin, code=CODE, state=failed_state)
        assert outcome(failed)[1] == {"tiktok": "failed", "reason": "connect_failed"}
        rewire(w, verify_first=TikTokFirstConnectVerifier(load_app=wiring(w).load_app))
        ok_state = state_of(await connect(w, admin))
        succeeded = await callback(admin, code=CODE, state=ok_state)
        assert outcome(succeeded)[1] == {"tiktok": "connected"}
    # The test's own httpx client logs the URLs it requests; that is not the server talking.
    server_logs = [r.getMessage() for r in caplog.records if r.name != "httpx"]
    rendered = " ".join(
        [
            *server_logs,
            repr(logs),
            failed.headers["location"],
            failed.text,
            succeeded.headers["location"],
            succeeded.text,
        ]
    )
    for secret in (
        leak,
        *FIRST_SECRETS,
        APP_SECRET,
        failed_state,
        failed_state[64:],
        ok_state,
        ok_state[64:],
        shop_id,
    ):
        assert secret not in rendered, secret
    assert any(entry.get("reason") == "connect_failed" for entry in logs)


async def connected_store(
    w: SessionWorld, monkeypatch: pytest.MonkeyPatch, admin: httpx.AsyncClient
) -> dict[str, Any]:
    """A really connected store: the callback ran against the mock, with the real job queue."""
    rewire(w, enqueue_job=enqueue_job)
    mock_tiktok(monkeypatch, [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-FIRST-CIPHER")])
    state = state_of(await connect(w, admin))
    assert outcome(await callback(admin, code=CODE, state=state))[1] == {"tiktok": "connected"}
    (store,) = await stores(w, w.tenants[0])
    return store


async def test_default_wiring_uses_the_real_queue_and_disconnect(world: SessionWorld) -> None:
    settings = configured(TIKTOK_APP_KEY="k", TIKTOK_APP_SECRET="s", TIKTOK_SERVICE_ID="i")
    built = build_tiktok_connect(settings, world.pool)
    assert built.enqueue_job is enqueue_job
    assert built.disconnect_store is disconnect_store


async def test_connect_queues_the_first_sync_in_the_real_queue(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    store = await connected_store(w, monkeypatch, await admin_of(w))
    jobs = await w.q(
        "SELECT kind,target,status FROM marketplace_sync_jobs WHERE store_id=%s ORDER BY kind",
        store["id"],
    )
    assert jobs == [
        {"kind": "backfill_orders", "target": None, "status": "pending"},
        {"kind": "backfill_products", "target": None, "status": "pending"},
    ]


async def test_catch_up_covers_a_connect_whose_first_sync_was_not_queued(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world

    async def refuse(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("synthetic queue outage")

    admin = await admin_of(w)
    mock_tiktok(monkeypatch, [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-FIRST-CIPHER")])
    rewire(w, enqueue_job=refuse)
    with capture_logs() as logs:
        state = state_of(await connect(w, admin))
        assert outcome(await callback(admin, code=CODE, state=state))[1] == {"tiktok": "connected"}
    assert any(entry.get("event") == "tiktok_first_sync_not_queued" for entry in logs)
    (store,) = await stores(w, w.tenants[0])
    assert await w.q("SELECT 1 FROM marketplace_sync_jobs WHERE store_id=%s", store["id"]) == []
    async with tx(w.pool, w.tenants[0]) as conn:
        result = await run_marketplace_catch_up(conn, UUID(w.tenants[0]), FakeClock())
    assert result == {"stores": 1, "queued": 1, "folded": 0}
    jobs = await w.q("SELECT kind,status FROM marketplace_sync_jobs WHERE store_id=%s", store["id"])
    assert jobs == [{"kind": "catch_up", "status": "pending"}]


async def test_disconnect_really_disconnects_the_store_for_an_admin(
    world: SessionWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world
    admin = await admin_of(w)
    store = await connected_store(w, monkeypatch, admin)
    path = f"/console/v1/business/stores/{store['id']}/disconnect"
    grants = "SELECT state FROM ecommerce_grants WHERE record_id=%s"
    assert [row["state"] for row in await w.q(grants, store["id"])] == ["active"]

    staff, _ = await w.member(role="tenant_staff")
    foreign = await admin_of(w, w.tenants[1])
    assert (await w.mutate(staff, path)).status_code == 403
    denied = await w.mutate(foreign, path)
    assert denied.status_code == 404 and denied.json()["detail"] == "store_not_found"
    assert await stores(w, w.tenants[0]) == [store]

    response = await w.mutate(admin, path)
    assert response.status_code == 204, response.text
    (after,) = await stores(w, w.tenants[0])
    assert after["disconnected_at"] is not None and after["access_token"] is None
    assert after["credential_generation"] == store["credential_generation"] + 1
    assert [row["state"] for row in await w.q(grants, store["id"])] == ["disconnected"]
    jobs = await w.q("SELECT status FROM marketplace_sync_jobs WHERE store_id=%s", store["id"])
    assert {row["status"] for row in jobs} == {"cancelled"} and len(jobs) == 2

    # Idempotent: the same Admin again, and a platform admin, both get 204 and change nothing.
    for repeat in (admin, w.admin):
        extra = f"?client_id={w.tenants[0]}" if repeat is w.admin else ""
        again = await w.mutate(repeat, path + extra)
        assert again.status_code == 204, again.text
    assert await stores(w, w.tenants[0]) == [after]


async def test_disconnect_of_a_pending_store_is_not_found(world: SessionWorld) -> None:
    w = world
    admin = await admin_of(w)
    state_of(await connect(w, admin))
    (pending,) = await stores(w, w.tenants[0])
    assert pending["shop_id"] is None
    response = await w.mutate(admin, f"/console/v1/business/stores/{pending['id']}/disconnect")
    assert response.status_code == 404 and response.json()["detail"] == "store_not_found"
    assert await stores(w, w.tenants[0]) == [pending]


async def test_disconnect_calls_the_injected_disconnect_store(world: SessionWorld) -> None:
    w = world
    rewire(w, disconnect_store=recorder(w).disconnect_store)
    admin = await admin_of(w)
    state_of(await connect(w, admin))
    (store,) = await stores(w, w.tenants[0])
    path = f"/console/v1/business/stores/{store['id']}/disconnect"
    foreign = await admin_of(w, w.tenants[1])
    for client in (foreign, admin):
        missing = await w.mutate(client, f"/console/v1/business/stores/{uuid4()}/disconnect")
        assert missing.status_code == 404 and missing.json()["detail"] == "store_not_found"
    assert (await w.mutate(foreign, path)).status_code == 404
    assert recorder(w).disconnects == []
    response = await w.mutate(admin, path)
    assert response.status_code == 204, response.text
    platform = await w.mutate(w.admin, f"{path}?client_id={w.tenants[0]}")
    assert platform.status_code == 204, platform.text
    assert recorder(w).disconnects == [(UUID(w.tenants[0]), store["id"], "admin")] * 2
