"""Token refresh as a verified event: synthetic transports and disposable databases only."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.agents.ecommerce import refresh, token_refresh
from csa.agents.ecommerce.app_loader import build_app_loader
from csa.agents.ecommerce.apps import transition_app
from csa.agents.ecommerce.authorization import PRODUCT_SCOPES, TikTokAuthorizationError
from csa.agents.ecommerce.connections import create_pending_store
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.grants import create_verified_grant, invalidate_grant, load_grant
from csa.agents.ecommerce.reader import read_verified_order
from csa.agents.ecommerce.refresh import list_refresh_due, refresh_store
from csa.agents.ecommerce.storage import (
    TikTokStoredCredentialResolver,
    create_verified_shop_binding,
)
from csa.agents.ecommerce.tiktok_models import TikTokOrderSnapshot
from csa.agents.ecommerce.token_refresh import refresh_tiktok_token
from csa.agents.ecommerce.token_response import parse_tiktok_refresh_response
from csa.core import db
from csa.core.crypto import credential_aad
from csa.core.tenancy import tx
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from tests.conftest import requires_docker
from tests.test_console_identity_config import configured
from tests.test_ecommerce_grants import seed
from tests.test_ecommerce_storage import decrypt, encrypt
from tests.test_tiktok_snapshots import order_snapshot

pytest_plugins = ["tests.test_ecommerce_grants"]

APP_KEY = "SYNTHETIC-REFRESH-APP-KEY"
APP_SECRET = "SYNTHETIC-REFRESH-APP-SECRET"
OLD_ACCESS = "SYNTHETIC-OLD-ACCESS"
OLD_REFRESH = "SYNTHETIC-OLD-REFRESH"
NEW_ACCESS = "SYNTHETIC-NEW-ACCESS"
NEW_REFRESH = "SYNTHETIC-NEW-REFRESH"
CIPHER = "SYNTHETIC-STORED-CIPHER"
OLD_SCOPES = frozenset({"seller.order.info", "seller.authorization.info", *PRODUCT_SCOPES})
# Different from OLD_SCOPES, so a test can tell response scopes from carried-over ones.
NEW_SCOPES = sorted({"seller.fs.order&fufillment.management", *OLD_SCOPES})
SECRETS = (APP_KEY, APP_SECRET, OLD_ACCESS, OLD_REFRESH, NEW_ACCESS, NEW_REFRESH, CIPHER)
DUE = timedelta(hours=24)


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body


def respond(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        stream=_BytesStream(json.dumps(payload).encode()),
        headers={"content-type": "application/json"},
    )


def token_payload(
    *,
    scopes: list[str] | None = NEW_SCOPES,
    refresh_token: str | None = NEW_REFRESH,
    refresh_in: timedelta = timedelta(days=60),
) -> dict:
    now = datetime.now(UTC)
    data: dict = {
        "access_token": NEW_ACCESS,
        "access_token_expire_in": int((now + timedelta(days=7)).timestamp()),
        "refresh_token_expire_in": int((now + refresh_in).timestamp()),
        "user_type": 0,
        "open_id": "SYNTHETIC-PRIVATE-OPEN-ID",
    }
    if refresh_token is not None:
        data["refresh_token"] = refresh_token
    if scopes is not None:
        data["granted_scopes"] = scopes
    return {"code": 0, "message": "SYNTHETIC-PRIVATE-MESSAGE", "data": data}


# Synthetic provider codes: one the tests treat as a known refusal, one that is unknown.
REFUSAL_CODE = 105002
UNKNOWN_CODE = 36009004


def rejected(request: httpx.Request) -> httpx.Response:
    return respond({"code": REFUSAL_CODE, "message": "SYNTHETIC-PRIVATE-DENIED"})


def unknown_code(request: httpx.Request) -> httpx.Response:
    return respond({"code": UNKNOWN_CODE, "message": "SYNTHETIC-PRIVATE-BUSY"})


def known_refusals(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the Partner Center refusal codes, which ship empty until rollout."""
    monkeypatch.setattr(refresh, "REFRESH_REFUSAL_CODES", frozenset({REFUSAL_CODE}))


class Provider:
    """Mock TikTok: a refresh handler and an authorized-shops handler, both counted."""

    def __init__(self, shop_id: str, *, token=None, shops=None) -> None:
        self.refresh_requests: list[httpx.Request] = []
        self.shop_requests: list[httpx.Request] = []
        self.token = token or (lambda request: respond(token_payload()))
        self.shops = shops or (
            lambda request: respond(
                {"code": 0, "data": {"shops": [{"id": shop_id, "cipher": CIPHER}]}}
            )
        )

    def install(self, monkeypatch: pytest.MonkeyPatch) -> "Provider":
        def refresh_handler(request: httpx.Request):
            self.refresh_requests.append(request)
            return self.token(request)

        def shop_handler(request: httpx.Request):
            self.shop_requests.append(request)
            return self.shops(request)

        monkeypatch.setattr(
            token_refresh, "_new_transport", lambda: httpx.MockTransport(refresh_handler)
        )
        monkeypatch.setattr(
            refresh, "_new_shop_transport", lambda: httpx.MockTransport(shop_handler)
        )
        return self


def credential_cell(value: str, column: str, record_id: UUID) -> str:
    return encrypt(value, credential_aad("channel_credentials", column, str(record_id)))


async def connected(
    owner: str, pool: AsyncConnectionPool, *, access_in: timedelta = timedelta(hours=1)
):
    """A tenant with an active app (revision 2) and one connected store at generation 2.

    seed() gives every store its own synthetic shop id (one active store per shop, 0491).
    """
    seeded = await seed(owner)
    record_id = seeded.context.binding.record_id
    async with await psycopg.AsyncConnection.connect(owner) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET access_token=%s, refresh_token=%s, "
            "expires_at=%s WHERE id=%s",
            (
                credential_cell(OLD_ACCESS, "access_token", record_id),
                credential_cell(OLD_REFRESH, "refresh_token", record_id),
                datetime.now(UTC) + access_in,
                record_id,
            ),
        )
    grant = seeded.model_copy(
        update={
            "context": seeded.context.model_copy(update={"credential_generation": 2}),
            "scopes": OLD_SCOPES,
        }
    )
    await create_verified_grant(pool, grant)
    await create_verified_shop_binding(pool, grant, SecretStr(CIPHER), encrypt=encrypt)
    return grant


def loader(pool: AsyncConnectionPool, app_id: str):
    settings = configured(
        TIKTOK_APP_KEY=APP_KEY,
        TIKTOK_APP_SECRET=APP_SECRET,
        TIKTOK_SERVICE_ID="SYNTHETIC-SERVICE-ID",
        TIKTOK_APP_ID=app_id,
    )
    return build_app_loader(settings, pool)


async def run_for(pool: AsyncConnectionPool, client_id: UUID, store_id: UUID, app_id: str):
    return await refresh_store(
        pool,
        client_id,
        store_id,
        load_app=loader(pool, app_id),
        encrypt=encrypt,
        decrypt=decrypt,
        clock=lambda: datetime.now(UTC),
    )


async def run(pool: AsyncConnectionPool, grant):
    binding = grant.context.binding
    return await run_for(pool, binding.client_id, binding.record_id, grant.context.app_id)


async def credential(pool: AsyncConnectionPool, grant) -> dict:
    binding = grant.context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        row = await (
            await conn.execute(
                "SELECT shop_id,access_token,refresh_token,expires_at,credential_generation,"
                "disconnected_at FROM channel_credentials WHERE id=%s",
                (binding.record_id,),
            )
        ).fetchone()
    assert row is not None
    return row


async def grants(pool: AsyncConnectionPool, grant) -> list[dict]:
    binding = grant.context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        return await (
            await conn.execute(
                "SELECT id,state,source,app_revision,credential_generation,scopes,expires_at "
                "FROM ecommerce_grants WHERE record_id=%s ORDER BY credential_generation",
                (binding.record_id,),
            )
        ).fetchall()


async def bindings(pool: AsyncConnectionPool, grant) -> list[dict]:
    binding = grant.context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        return await (
            await conn.execute(
                "SELECT b.grant_id,b.shop_cipher FROM ecommerce_shop_bindings b "
                "JOIN ecommerce_grants g ON g.client_id=b.client_id AND g.id=b.grant_id "
                "WHERE g.record_id=%s",
                (binding.record_id,),
            )
        ).fetchall()


async def store_status(pool: AsyncConnectionPool, grant) -> str:
    """The status the Store connections page shows, from the real status query."""
    binding = grant.context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        rows = await db.q.fetch_all(
            "business/store_connections.sql",
            {
                "client_id": binding.client_id,
                "ids": [binding.record_id],
                "product_scopes": sorted(PRODUCT_SCOPES),
            },
            conn=conn,
        )
    assert len(rows) == 1
    return rows[0]["status"]


async def due(pool: AsyncConnectionPool, client_id: UUID) -> list[UUID]:
    async with tx(pool, str(client_id)) as conn:
        return await list_refresh_due(conn, client_id, within=DUE)


def plain(value: str, column: str, record_id: UUID) -> str:
    return decrypt(value, credential_aad("channel_credentials", column, str(record_id)))


def assert_no_secret(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text
    assert "SYNTHETIC-PRIVATE" not in text


async def _slow(request: httpx.Request) -> httpx.Response:
    await asyncio.sleep(5)
    return respond(token_payload())


def _drop(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadError("SYNTHETIC-PRIVATE-DROPPED", request=request)


async def call_refresh() -> object:
    return await refresh_tiktok_token(
        app_key=SecretStr(APP_KEY),
        app_secret=SecretStr(APP_SECRET),
        refresh_token=SecretStr(OLD_REFRESH),
        now=datetime.now(UTC),
    )


# --- transport and parser (no database) ---


async def test_refresh_request_uses_one_get_with_the_placeholder_contract(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = Provider("unused").install(monkeypatch)
    caplog.set_level(logging.DEBUG)

    result = await refresh_tiktok_token(
        app_key=SecretStr(APP_KEY),
        app_secret=SecretStr(APP_SECRET),
        refresh_token=SecretStr(OLD_REFRESH),
        now=datetime.now(UTC),
    )

    assert len(provider.refresh_requests) == 1
    request = provider.refresh_requests[0]
    assert request.method == "GET"
    assert request.url == httpx.URL(
        token_refresh.REFRESH_ENDPOINT,
        params=[
            ("app_key", APP_KEY),
            ("app_secret", APP_SECRET),
            ("refresh_token", OLD_REFRESH),
            ("grant_type", "refresh_token"),
        ],
    )
    assert token_refresh.REFRESH_ENDPOINT.endswith("/api/v2/token/refresh")
    assert request.headers["accept-encoding"] == "identity"
    assert result.access_token.get_secret_value() == NEW_ACCESS
    assert result.refresh_token.get_secret_value() == NEW_REFRESH
    assert result.scopes == frozenset(NEW_SCOPES)
    assert_no_secret(caplog.text + repr(result))


@pytest.mark.parametrize(
    ("handler", "code"),
    [
        (rejected, "token_refresh_rejected"),
        (lambda r: respond({"code": 0}, status_code=503), "token_refresh_http_rejected"),
        (lambda r: respond(token_payload(refresh_token=None)), "invalid_token_response"),
        (lambda r: respond(token_payload(scopes=[])), "insufficient_scope"),
        (_drop, "token_refresh_transport_failed"),
    ],
    ids=["rejected", "http_503", "no_refresh_token", "empty_scopes", "dropped"],
)
async def test_refresh_failures_are_single_attempt_safe_codes(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, handler, code: str
) -> None:
    provider = Provider("unused", token=handler).install(monkeypatch)
    caplog.set_level(logging.DEBUG)

    with pytest.raises(TikTokAuthorizationError) as caught:
        await call_refresh()

    assert caught.value.code == code
    assert len(provider.refresh_requests) == 1
    assert caught.value.__cause__ is None
    assert_no_secret(str(caught.value) + repr(caught.value) + caplog.text)


def test_refresh_parser_marks_omitted_scopes_empty_and_keeps_present_ones_exact() -> None:
    now = datetime.now(UTC)
    assert parse_tiktok_refresh_response(token_payload(scopes=None), now=now).scopes == frozenset()
    assert parse_tiktok_refresh_response(token_payload(), now=now).scopes == frozenset(NEW_SCOPES)
    with pytest.raises(TikTokAuthorizationError, match="insufficient_scope"):
        parse_tiktok_refresh_response(token_payload(scopes=["synthetic.other"]), now=now)
    with pytest.raises(TikTokAuthorizationError, match="invalid_token_response"):
        parse_tiktok_refresh_response(token_payload(scopes=[" seller.order.info"]), now=now)


# --- refresh_store and list_refresh_due (disposable database) ---


@requires_docker
async def test_refresh_success_bumps_generation_and_records_refresh_grant(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = await connected(migrated_dsn, pool)
    binding = old.context.binding
    tenant = binding.client_id
    provider = Provider(binding.shop_id).install(monkeypatch)
    assert await due(pool, tenant) == [binding.record_id]
    before = datetime.now(UTC)

    assert await run(pool, old) == "refreshed"

    assert len(provider.refresh_requests) == len(provider.shop_requests) == 1
    assert provider.refresh_requests[0].url.params["refresh_token"] == OLD_REFRESH
    shop_request = provider.shop_requests[0]
    assert shop_request.url.path == "/authorization/202309/shops"
    assert shop_request.headers["x-tts-access-token"] == NEW_ACCESS
    row = await credential(pool, old)
    assert row["credential_generation"] == old.context.credential_generation + 1
    assert plain(row["access_token"], "access_token", binding.record_id) == NEW_ACCESS
    assert plain(row["refresh_token"], "refresh_token", binding.record_id) == NEW_REFRESH
    assert row["expires_at"] > before + timedelta(days=6)
    assert row["shop_id"] == binding.shop_id and row["disconnected_at"] is None

    assert (await load_grant(pool, tenant, old.id)).state == "disconnected"
    first, second = await grants(pool, old)
    assert first["id"] == old.id
    assert second["state"] == "active"
    assert second["source"] == "verified_token_refresh"
    assert second["credential_generation"] == row["credential_generation"]
    assert second["app_revision"] == old.context.app_revision
    assert frozenset(second["scopes"]) == frozenset(NEW_SCOPES)
    # Access expiry (7 days) is earlier than refresh expiry (60 days).
    assert second["expires_at"] == row["expires_at"]
    new_bindings = [b for b in await bindings(pool, old) if b["grant_id"] == second["id"]]
    assert len(new_bindings) == 1
    assert (
        decrypt(
            new_bindings[0]["shop_cipher"],
            credential_aad("ecommerce_shop_bindings", "shop_cipher", str(second["id"])),
        )
        == CIPHER
    )
    assert await store_status(pool, old) == "authorization_recorded"
    assert await due(pool, tenant) == []

    # The refreshed grant reads end to end with the new token and the stored cipher.
    operation = await begin_read(
        pool, tenant, second["id"], TikTokReadRequest(kind="detail", order_id="SYNTHETIC-1")
    )
    reads = []

    def read_handler(request: httpx.Request) -> httpx.Response:
        reads.append(request.url.path)
        assert request.headers["x-tts-access-token"] == NEW_ACCESS
        if request.url.path == "/authorization/202309/shops":
            return respond(
                {"code": 0, "data": {"shops": [{"id": binding.shop_id, "cipher": CIPHER}]}}
            )
        assert request.url.params["shop_cipher"] == CIPHER
        return respond({"code": 0, "data": {"orders": [order_snapshot()]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(read_handler)) as http:
        resolver = TikTokStoredCredentialResolver(
            load_app=loader(pool, old.context.app_id), decrypt=decrypt, http_client=http
        )
        result = await read_verified_order(pool, tenant, operation.id, resolver)
    assert isinstance(result, TikTokOrderSnapshot)
    assert result.id == "SYNTHETIC-1"
    assert reads == ["/authorization/202309/shops", "/order/202309/orders"]


@requires_docker
async def test_refresh_rejected_marks_store_expired_without_retry(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    known_refusals(monkeypatch)
    old = await connected(migrated_dsn, pool)
    binding = old.context.binding
    provider = Provider(binding.shop_id, token=rejected).install(monkeypatch)

    assert await run(pool, old) == "expired"

    assert len(provider.refresh_requests) == 1
    assert provider.shop_requests == []
    row = await credential(pool, old)
    assert row["credential_generation"] == old.context.credential_generation + 1
    assert row["access_token"] is None and row["refresh_token"] is None
    assert row["expires_at"] <= datetime.now(UTC)
    assert row["disconnected_at"] is None
    assert [g["id"] for g in await grants(pool, old)] == [old.id]
    assert (await load_grant(pool, binding.client_id, old.id)).state == "disconnected"
    assert await store_status(pool, old) == "expired"
    assert await due(pool, binding.client_id) == []
    # The next run never tries again: the store waits for a reconnect.
    assert await run(pool, old) == "skipped"
    assert len(provider.refresh_requests) == 1


@requires_docker
@pytest.mark.parametrize("case", ["shop_missing", "cipher_changed", "scopes_reduced"])
async def test_refresh_shop_mismatch_marks_expired_keeps_nothing_new(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    old = await connected(migrated_dsn, pool)
    binding = old.context.binding
    shops = {
        "shop_missing": [{"id": f"SYNTHETIC-OTHER-{uuid4()}", "cipher": "SYNTHETIC-OTHER"}],
        "cipher_changed": [{"id": binding.shop_id, "cipher": "SYNTHETIC-CHANGED-CIPHER"}],
        "scopes_reduced": [{"id": binding.shop_id, "cipher": CIPHER}],
    }[case]
    token = (
        (lambda r: respond(token_payload(scopes=["seller.order.info"])))
        if case == "scopes_reduced"
        else None
    )
    provider = Provider(
        binding.shop_id,
        token=token,
        shops=lambda r: respond({"code": 0, "data": {"shops": shops}}),
    ).install(monkeypatch)

    assert await run(pool, old) == "expired"

    assert len(provider.refresh_requests) == 1
    # A grant without the order and shop permissions is refused before any shop lookup.
    assert len(provider.shop_requests) == (0 if case == "scopes_reduced" else 1)
    row = await credential(pool, old)
    assert row["access_token"] is None and row["refresh_token"] is None
    assert row["credential_generation"] == old.context.credential_generation + 1
    assert [g["id"] for g in await grants(pool, old)] == [old.id]
    assert [b["grant_id"] for b in await bindings(pool, old)] == [old.id]
    assert await store_status(pool, old) == "expired"
    assert await due(pool, binding.client_id) == []


@requires_docker
@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("refresh_dropped", "token_refresh_transport_failed"),
        ("refresh_timeout", "token_refresh_timeout"),
        ("refresh_http_503", "token_refresh_http_rejected"),
        ("refresh_without_refresh_token", "invalid_token_response"),
        ("shops_dropped", "authorized_shop_verification_failed"),
        ("shops_http_500", "authorized_shop_verification_failed"),
        # Shipped default: no refusal codes are known yet, so a refusal is not trusted.
        ("refusal_code_by_default", "token_refresh_rejected"),
        # A code outside the known refusal codes (rate limit, internal error) is lost too.
        ("unknown_code", "token_refresh_rejected"),
    ],
)
async def test_lost_response_keeps_old_credential_and_next_run_retries(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    code: str,
) -> None:
    old = await connected(migrated_dsn, pool)
    binding = old.context.binding
    lost = {
        "refresh_dropped": {"token": _drop},
        "refresh_timeout": {"token": _slow},
        "refresh_http_503": {"token": lambda r: respond({"code": 0}, status_code=503)},
        "refresh_without_refresh_token": {
            "token": lambda r: respond(token_payload(refresh_token=None))
        },
        "shops_dropped": {"shops": _drop},
        "shops_http_500": {"shops": lambda r: respond({"code": 0}, status_code=500)},
        "refusal_code_by_default": {"token": rejected},
        "unknown_code": {"token": unknown_code},
    }[case]
    if case == "unknown_code":
        known_refusals(monkeypatch)
    else:
        assert refresh.REFRESH_REFUSAL_CODES == frozenset()
    if case == "refresh_timeout":
        monkeypatch.setattr(refresh, "REFRESH_TIMEOUT_SECONDS", 0.2)
    provider = Provider(binding.shop_id, **lost).install(monkeypatch)
    before = await credential(pool, old)

    with pytest.raises(TikTokAuthorizationError) as caught:
        await run(pool, old)

    assert caught.value.code == code
    assert caught.value.__cause__ is None
    assert len(provider.refresh_requests) == 1
    assert await credential(pool, old) == before
    assert [g["id"] for g in await grants(pool, old)] == [old.id]
    assert (await load_grant(pool, binding.client_id, old.id)).state == "active"
    assert await store_status(pool, old) == "authorization_recorded"
    assert await due(pool, binding.client_id) == [binding.record_id]

    # The next run tries again, from the same stored refresh token, and succeeds.
    retry = Provider(binding.shop_id).install(monkeypatch)
    assert await run(pool, old) == "refreshed"
    assert len(retry.refresh_requests) == 1
    assert retry.refresh_requests[0].url.params["refresh_token"] == OLD_REFRESH
    assert (await credential(pool, old))["credential_generation"] == (
        before["credential_generation"] + 1
    )


@requires_docker
async def test_scopes_carried_over_when_response_omits_them(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = await connected(migrated_dsn, pool)
    Provider(
        old.context.binding.shop_id, token=lambda r: respond(token_payload(scopes=None))
    ).install(monkeypatch)

    assert await run(pool, old) == "refreshed"

    _, second = await grants(pool, old)
    assert frozenset(second["scopes"]) == old.scopes
    assert second["source"] == "verified_token_refresh"
    assert await store_status(pool, old) == "authorization_recorded"


@requires_docker
async def test_rotation_refresh_moves_grant_to_new_app_revision(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The access token is far from expiry, so only the app revision makes the store due.
    old = await connected(migrated_dsn, pool, access_in=timedelta(days=5))
    binding = old.context.binding
    tenant = binding.client_id
    provider = Provider(binding.shop_id).install(monkeypatch)
    assert await due(pool, tenant) == []
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        disabled = await transition_app(
            conn, app_id=old.context.app_id, expected_revision=2, state="disabled"
        )
        await conn.commit()
        # While the app is disabled (secret being swapped) nothing is refreshed.
        assert await due(pool, tenant) == []
        assert await run(pool, old) == "skipped"
        assert provider.refresh_requests == []
        enabled = await transition_app(
            conn, app_id=old.context.app_id, expected_revision=disabled.revision, state="active"
        )
    assert enabled.revision == 4
    assert await store_status(pool, old) == "app_unavailable"
    assert await due(pool, tenant) == [binding.record_id]

    assert await run(pool, old) == "refreshed"

    _, second = await grants(pool, old)
    assert second["app_revision"] == enabled.revision
    assert second["state"] == "active" and second["source"] == "verified_token_refresh"
    assert await store_status(pool, old) == "authorization_recorded"
    assert await due(pool, tenant) == []


@requires_docker
async def test_refresh_logs_no_token(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    known_refusals(monkeypatch)
    good = await connected(migrated_dsn, pool)
    Provider(good.context.binding.shop_id).install(monkeypatch)
    assert await run(pool, good) == "refreshed"

    refused = await connected(migrated_dsn, pool)
    Provider(refused.context.binding.shop_id, token=rejected).install(monkeypatch)
    assert await run(pool, refused) == "expired"

    lost = await connected(migrated_dsn, pool)
    Provider(lost.context.binding.shop_id, shops=_drop).install(monkeypatch)
    with pytest.raises(TikTokAuthorizationError) as caught:
        await run(pool, lost)

    text = caplog.text + str(caught.value) + repr(caught.value)
    assert_no_secret(text)
    for store in (good, refused, lost):
        assert store.context.binding.shop_id not in text


@requires_docker
async def test_pending_disconnected_and_revoked_stores_are_never_refreshed(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    disconnected = await connected(migrated_dsn, pool)
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s",
            (disconnected.context.binding.record_id,),
        )
    revoked = await connected(migrated_dsn, pool)
    await invalidate_grant(
        pool, revoked.context.binding.client_id, revoked.id, expected_revision=1, state="revoked"
    )
    neighbour = await connected(migrated_dsn, pool)
    tenant = neighbour.context.binding.client_id
    async with tx(pool, str(tenant)) as conn:
        pending_id = await create_pending_store(conn, tenant)
    provider = Provider("unused").install(monkeypatch)

    for store in (disconnected, revoked):
        assert await due(pool, store.context.binding.client_id) == []
        assert await run(pool, store) == "skipped"
    assert await due(pool, tenant) == [neighbour.context.binding.record_id]
    assert await run_for(pool, tenant, pending_id, neighbour.context.app_id) == "skipped"
    # Another tenant's store is invisible: nothing is refreshed across tenants.
    other_tenant = disconnected.context.binding.client_id
    assert (
        await run_for(
            pool, other_tenant, neighbour.context.binding.record_id, neighbour.context.app_id
        )
        == "skipped"
    )
    assert provider.refresh_requests == [] and provider.shop_requests == []
    assert (await load_grant(pool, tenant, neighbour.id)).state == "active"


@requires_docker
async def test_concurrent_refreshes_of_one_store_call_the_provider_once(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = await connected(migrated_dsn, pool)
    provider = Provider(old.context.binding.shop_id).install(monkeypatch)

    outcomes = await asyncio.gather(run(pool, old), run(pool, old))

    assert sorted(outcomes) == ["refreshed", "skipped"]
    assert len(provider.refresh_requests) == 1
    assert (await credential(pool, old))["credential_generation"] == (
        old.context.credential_generation + 1
    )


@requires_docker
@pytest.mark.parametrize("change", ["grant_revoked", "credential_changed", "app_rotated"])
async def test_store_changed_while_refresh_starts_is_skipped_without_provider_call(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    old = await connected(migrated_dsn, pool)
    binding = old.context.binding
    provider = Provider(binding.shop_id).install(monkeypatch)
    real_load = loader(pool, old.context.app_id)

    async def load_app_after_change(app_id: str):
        # Runs after the unlocked read and before the app, credential and grant fences.
        app = await real_load(app_id)
        if change == "grant_revoked":
            await invalidate_grant(
                pool, binding.client_id, old.id, expected_revision=1, state="revoked"
            )
        else:
            async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
                if change == "credential_changed":
                    await conn.execute(
                        "UPDATE channel_credentials SET meta='{\"synthetic\":1}' WHERE id=%s",
                        (binding.record_id,),
                    )
                else:
                    await transition_app(conn, app_id=app_id, expected_revision=2, state="disabled")
        return app

    outcome = await refresh_store(
        pool,
        binding.client_id,
        binding.record_id,
        load_app=load_app_after_change,
        encrypt=encrypt,
        decrypt=decrypt,
    )

    assert outcome == "skipped"
    assert provider.refresh_requests == [] and provider.shop_requests == []
    assert [g["id"] for g in await grants(pool, old)] == [old.id]


def test_provider_refusal_carries_only_the_safe_integer_code() -> None:
    from csa.agents.ecommerce.token_response import TikTokProviderRejection

    payload = {"code": REFUSAL_CODE, "message": "SYNTHETIC-PRIVATE-DENIED"}
    with pytest.raises(TikTokProviderRejection) as caught:
        parse_tiktok_refresh_response(payload, now=datetime.now(UTC))
    assert caught.value.code == "token_refresh_rejected"
    assert caught.value.provider_code == REFUSAL_CODE
    assert type(caught.value.provider_code) is int
    assert "SYNTHETIC-PRIVATE" not in str(caught.value) + repr(caught.value)
    assert str(REFUSAL_CODE) not in str(caught.value)


@requires_docker
async def test_grant_expiry_is_the_earlier_of_access_and_refresh_expiry(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The refresh token here expires (2 days) before the access token (7 days).
    old = await connected(migrated_dsn, pool)
    Provider(
        old.context.binding.shop_id,
        token=lambda r: respond(token_payload(refresh_in=timedelta(days=2))),
    ).install(monkeypatch)
    before = datetime.now(UTC)

    assert await run(pool, old) == "refreshed"

    row = await credential(pool, old)
    _, second = await grants(pool, old)
    assert row["expires_at"] > before + timedelta(days=6)  # the credential keeps access expiry
    assert before + timedelta(days=1) < second["expires_at"] < before + timedelta(days=3)
    assert second["expires_at"] < row["expires_at"]
