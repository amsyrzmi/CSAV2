"""Offline authorization attempts with synthetic exchange, crypto and disposable DB only."""

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.agents.ecommerce import authorization_verifier, connections, token_exchange
from csa.agents.ecommerce.apps import register_app, transition_app
from csa.agents.ecommerce.authorization import (
    PRODUCT_SCOPES,
    TikTokAuthorizationContext,
    TikTokAuthorizationError,
    TikTokFirstConnectContext,
    TikTokGrantState,
)
from csa.agents.ecommerce.authorization_verifier import (
    TikTokAuthorizationVerifier,
    TikTokFirstConnectVerifier,
)
from csa.agents.ecommerce.connections import (
    AuthorizationAttempt,
    VerifiedAuthorizationResult,
    VerifiedFirstConnectResult,
    begin_authorization,
    begin_first_authorization,
    cancel_authorization,
    complete_authorization,
    complete_first_authorization,
    create_pending_store,
)
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.grants import create_verified_grant, invalidate_grant, load_grant
from csa.agents.ecommerce.reader import read_verified_order
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.storage import (
    TikTokStoredCredentialResolver,
    create_verified_shop_binding,
)
from csa.agents.ecommerce.stores import PendingStoreBinding, StoreBinding
from csa.core.crypto import CryptoError, credential_aad
from csa.core.tenancy import tx
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr, ValidationError

from tests.conftest import requires_docker
from tests.test_ecommerce_grants import events
from tests.test_ecommerce_storage import app, decrypt, encrypt, prepare
from tests.test_tiktok_snapshots import order_snapshot

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]


class _BytesStream(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body


async def setup(pool, owner):
    grant = await prepare(owner)
    await create_verified_grant(pool, grant)
    await create_verified_shop_binding(
        pool, grant, SecretStr("synthetic-old-cipher"), encrypt=encrypt
    )
    return grant


def verified(context):
    now = datetime.now(UTC)
    return VerifiedAuthorizationResult(
        context=context,
        access_token=SecretStr("synthetic-new-access"),
        refresh_token=SecretStr("synthetic-new-refresh"),
        shop_cipher=SecretStr("synthetic-new-cipher"),
        access_expires_at=now + timedelta(hours=1),
        grant_expires_at=now + timedelta(hours=2),
        verified_at=now,
        scopes=frozenset({"seller.order.info", "seller.authorization.info"}),
    )


async def snapshot(pool, grant):
    binding = grant.context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        row = await (
            await conn.execute(
                "SELECT access_token,refresh_token,expires_at,credential_generation,meta "
                "FROM channel_credentials WHERE id=%s",
                (binding.record_id,),
            )
        ).fetchone()
        assert row is not None
        return row


async def status(pool, tenant, attempt_id):
    async with tx(pool, str(tenant)) as conn:
        row = await (
            await conn.execute(
                "SELECT status,grant_id FROM ecommerce_authorization_attempts WHERE id=%s",
                (attempt_id,),
            )
        ).fetchone()
        assert row is not None
        return row


async def pending(pool, owner):
    """A tenant with an active app and one pending TikTok store (no shop yet)."""
    client_id, app_id = uuid4(), f"SYNTH-{uuid4()}"
    async with await psycopg.AsyncConnection.connect(owner) as conn:
        await register_app(conn, app_id)
        await transition_app(conn, app_id=app_id, expected_revision=1, state="active")
        await conn.execute(
            "INSERT INTO clients(id,slug,name,platform_version) VALUES (%s,%s,'Synthetic','test')",
            (client_id, f"synthetic-{client_id}"),
        )
    async with tx(pool, str(client_id)) as conn:
        record_id = await create_pending_store(conn, client_id)
    return TikTokFirstConnectContext(
        binding=PendingStoreBinding(
            client_id=client_id, record_id=record_id, platform="tiktok_shop"
        ),
        app_id=app_id,
        app_revision=2,
        credential_generation=1,
    )


async def pending_row(pool, context):
    binding = context.binding
    async with tx(pool, str(binding.client_id)) as conn:
        row = await (
            await conn.execute(
                "SELECT channel,shop_id,access_token,refresh_token,expires_at,"
                "credential_generation,disconnected_at FROM channel_credentials WHERE id=%s",
                (binding.record_id,),
            )
        ).fetchone()
        assert row is not None
        return row


async def attempts(pool, tenant):
    async with tx(pool, str(tenant)) as conn:
        return await (
            await conn.execute(
                "SELECT id,status,state_hash,context::text AS context "
                "FROM ecommerce_authorization_attempts ORDER BY created_at"
            )
        ).fetchall()


async def test_complete_authorization_composes_mocked_tiktok_verifier_end_to_end(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    app_key = "SYNTHETIC-APP-KEY"
    app_secret = "SYNTHETIC-APP-SECRET"
    authorization_code = "SYNTHETIC-AUTHORIZATION-CODE"
    app_material = TikTokAppMaterial(
        app_id=old.context.app_id,
        revision=old.context.app_revision,
        app_key=SecretStr(app_key),
        app_secret=SecretStr(app_secret),
        enabled=True,
    )
    assert app_material.revision == old.context.app_revision == 2
    active_attempt: list[UUID | None] = [None]
    exchange_requests = []
    shop_requests = []
    events_seen = []
    app_loads = []
    caplog.set_level(logging.DEBUG)

    def token_handler(request: httpx.Request) -> httpx.Response:
        exchange_requests.append(request)
        with psycopg.connect(migrated_dsn) as conn:
            consumed = conn.execute(
                "SELECT status FROM ecommerce_authorization_attempts WHERE id=%s",
                (active_attempt[0],),
            ).fetchone()
        assert consumed == ("consumed",)
        expected_params = [
            ("app_key", app_key),
            ("app_secret", app_secret),
            ("auth_code", authorization_code),
            ("grant_type", "authorized_code"),
        ]
        assert request.method == "GET"
        assert request.url == httpx.URL(token_exchange.TOKEN_ENDPOINT, params=expected_params)
        events_seen.append(("exchange", active_attempt[0]))
        exchange_number = len(exchange_requests)
        now = datetime.now(UTC)
        payload = {
            "code": 0,
            "data": {
                "access_token": f"SYNTHETIC-ACCESS-{exchange_number}",
                "refresh_token": f"SYNTHETIC-REFRESH-{exchange_number}",
                "access_token_expire_in": int((now + timedelta(hours=1)).timestamp()),
                "refresh_token_expire_in": int((now + timedelta(days=30)).timestamp()),
                "user_type": 0,
                "granted_scopes": [
                    "seller.order.info",
                    "seller.authorization.info",
                ],
            },
        }
        return httpx.Response(
            200,
            stream=_BytesStream(json.dumps(payload).encode()),
            headers={"content-type": "application/json"},
        )

    def shop_handler(request: httpx.Request) -> httpx.Response:
        shop_requests.append(request)
        shop_number = len(shop_requests)
        assert request.method == "GET"
        assert request.url.path == "/authorization/202309/shops"
        assert request.headers["x-tts-access-token"] == f"SYNTHETIC-ACCESS-{shop_number}"
        assert "shop_cipher" not in request.url.params
        events_seen.append(("shops", active_attempt[0]))
        shops = [{"id": "SYNTHETIC-OTHER-SHOP", "cipher": "SYNTHETIC-OTHER-CIPHER"}]
        if shop_number != 2:
            shops.append(
                {
                    "id": old.context.binding.shop_id,
                    "cipher": "SYNTHETIC-INTENDED-CIPHER",
                }
            )
        payload = {"code": 0, "data": {"shops": shops}}
        if shop_number == 2:
            payload["message"] = "SYNTHETIC-PRIVATE-PROVIDER-DETAIL"
        return httpx.Response(
            200,
            stream=_BytesStream(json.dumps(payload).encode()),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr(
        token_exchange, "_new_transport", lambda: httpx.MockTransport(token_handler)
    )
    monkeypatch.setattr(
        authorization_verifier,
        "_new_shop_transport",
        lambda: httpx.MockTransport(shop_handler),
    )

    async def load_app(app_id: str) -> TikTokAppMaterial:
        app_loads.append((app_id, app_material.revision))
        return app_material

    verifier = TikTokAuthorizationVerifier(load_app=load_app)
    first = await begin_authorization(pool, old.context)
    active_attempt[0] = first.attempt.id
    completed = await complete_authorization(
        pool,
        tenant,
        first.attempt.id,
        state=first.state,
        authorization_code=SecretStr(authorization_code),
        verify=verifier,
        encrypt=encrypt,
    )

    assert events_seen == [
        ("exchange", first.attempt.id),
        ("shops", first.attempt.id),
    ]
    assert len(exchange_requests) == len(shop_requests) == 1
    assert app_loads == [(old.context.app_id, old.context.app_revision)]
    assert completed.context.credential_generation == old.context.credential_generation + 1
    assert (await load_grant(pool, tenant, old.id)).state == "disconnected"
    assert await load_grant(pool, tenant, completed.id) == completed
    assert await status(pool, tenant, first.attempt.id) == {
        "status": "completed",
        "grant_id": completed.id,
    }

    persisted = await snapshot(pool, completed)
    assert persisted["access_token"].startswith("v1:")
    assert persisted["refresh_token"].startswith("v1:")
    assert (
        decrypt(
            persisted["access_token"],
            credential_aad(
                "channel_credentials", "access_token", str(old.context.binding.record_id)
            ),
        )
        == "SYNTHETIC-ACCESS-1"
    )
    assert (
        decrypt(
            persisted["refresh_token"],
            credential_aad(
                "channel_credentials", "refresh_token", str(old.context.binding.record_id)
            ),
        )
        == "SYNTHETIC-REFRESH-1"
    )
    async with tx(pool, str(tenant)) as conn:
        binding = await (
            await conn.execute(
                "SELECT shop_cipher FROM ecommerce_shop_bindings "
                "WHERE client_id=%s AND grant_id=%s",
                (tenant, completed.id),
            )
        ).fetchone()
    assert binding is not None
    assert binding["shop_cipher"].startswith("v1:")
    assert (
        decrypt(
            binding["shop_cipher"],
            credential_aad("ecommerce_shop_bindings", "shop_cipher", str(completed.id)),
        )
        == "SYNTHETIC-INTENDED-CIPHER"
    )

    before_failed_reconnect = await snapshot(pool, completed)
    before_failed_grant = await load_grant(pool, tenant, completed.id)
    before_failed_events = await events(pool, tenant, completed.id)
    before_failed_binding = binding["shop_cipher"]
    failed = await begin_authorization(pool, completed.context)
    active_attempt[0] = failed.attempt.id
    with pytest.raises(
        TikTokAuthorizationError, match="authorized_shop_verification_failed"
    ) as failure:
        await complete_authorization(
            pool,
            tenant,
            failed.attempt.id,
            state=failed.state,
            authorization_code=SecretStr(authorization_code),
            verify=verifier,
            encrypt=encrypt,
        )

    safe_failure = str(failure.value) + caplog.text
    for secret in (
        app_key,
        app_secret,
        authorization_code,
        "SYNTHETIC-ACCESS-2",
        "SYNTHETIC-REFRESH-2",
        "SYNTHETIC-INTENDED-CIPHER",
        "SYNTHETIC-PRIVATE-PROVIDER-DETAIL",
    ):
        assert secret not in safe_failure
    assert failure.value.__suppress_context__
    assert await snapshot(pool, completed) == before_failed_reconnect
    assert await load_grant(pool, tenant, completed.id) == before_failed_grant
    assert await events(pool, tenant, completed.id) == before_failed_events
    assert await status(pool, tenant, failed.attempt.id) == {
        "status": "consumed",
        "grant_id": None,
    }
    async with tx(pool, str(tenant)) as conn:
        unchanged_binding = await (
            await conn.execute(
                "SELECT shop_cipher FROM ecommerce_shop_bindings "
                "WHERE client_id=%s AND grant_id=%s",
                (tenant, completed.id),
            )
        ).fetchone()
    assert unchanged_binding == {"shop_cipher": before_failed_binding}

    with pytest.raises(TikTokAuthorizationError):
        await complete_authorization(
            pool,
            tenant,
            failed.attempt.id,
            state=failed.state,
            authorization_code=SecretStr(authorization_code),
            verify=verifier,
            encrypt=encrypt,
        )
    assert len(exchange_requests) == len(shop_requests) == 2
    assert app_loads == [
        (old.context.app_id, old.context.app_revision),
        (old.context.app_id, old.context.app_revision),
    ]
    assert events_seen == [
        ("exchange", first.attempt.id),
        ("shops", first.attempt.id),
        ("exchange", failed.attempt.id),
        ("shops", failed.attempt.id),
    ]

    async with tx(pool, str(tenant)) as conn:
        counts_before_persistence_failure = await (
            await conn.execute(
                "SELECT (SELECT count(*) FROM ecommerce_grants WHERE client_id=%s) AS grants, "
                "(SELECT count(*) FROM ecommerce_shop_bindings WHERE client_id=%s) AS bindings",
                (tenant, tenant),
            )
        ).fetchone()

    def fail_shop_binding_encryption(value: str, aad: bytes) -> str:
        if b".shop_cipher:" in aad:
            raise CryptoError("SYNTHETIC-PRIVATE-CRYPTO-DETAIL")
        return encrypt(value, aad)

    before_persistence_failure = await snapshot(pool, completed)
    persistence_failure_attempt = await begin_authorization(pool, completed.context)
    active_attempt[0] = persistence_failure_attempt.attempt.id
    with pytest.raises(TikTokAuthorizationError, match="shop_cipher_encryption_failed") as caught:
        await complete_authorization(
            pool,
            tenant,
            persistence_failure_attempt.attempt.id,
            state=persistence_failure_attempt.state,
            authorization_code=SecretStr(authorization_code),
            verify=verifier,
            encrypt=fail_shop_binding_encryption,
        )
    assert "SYNTHETIC-PRIVATE-CRYPTO-DETAIL" not in str(caught.value) + caplog.text
    assert await snapshot(pool, completed) == before_persistence_failure
    assert await load_grant(pool, tenant, completed.id) == before_failed_grant
    assert await events(pool, tenant, completed.id) == before_failed_events
    assert await status(pool, tenant, persistence_failure_attempt.attempt.id) == {
        "status": "consumed",
        "grant_id": None,
    }
    async with tx(pool, str(tenant)) as conn:
        counts_after_persistence_failure = await (
            await conn.execute(
                "SELECT (SELECT count(*) FROM ecommerce_grants WHERE client_id=%s) AS grants, "
                "(SELECT count(*) FROM ecommerce_shop_bindings WHERE client_id=%s) AS bindings",
                (tenant, tenant),
            )
        ).fetchone()
        unchanged_binding = await (
            await conn.execute(
                "SELECT shop_cipher FROM ecommerce_shop_bindings "
                "WHERE client_id=%s AND grant_id=%s",
                (tenant, completed.id),
            )
        ).fetchone()
    assert counts_after_persistence_failure == counts_before_persistence_failure
    assert unchanged_binding == {"shop_cipher": before_failed_binding}
    with pytest.raises(TikTokAuthorizationError):
        await complete_authorization(
            pool,
            tenant,
            persistence_failure_attempt.attempt.id,
            state=persistence_failure_attempt.state,
            authorization_code=SecretStr(authorization_code),
            verify=verifier,
            encrypt=encrypt,
        )
    assert len(exchange_requests) == len(shop_requests) == 3
    assert app_loads == [
        (old.context.app_id, old.context.app_revision),
        (old.context.app_id, old.context.app_revision),
        (old.context.app_id, old.context.app_revision),
    ]
    assert events_seen == [
        ("exchange", first.attempt.id),
        ("shops", first.attempt.id),
        ("exchange", failed.attempt.id),
        ("shops", failed.attempt.id),
        ("exchange", persistence_failure_attempt.attempt.id),
        ("shops", persistence_failure_attempt.attempt.id),
    ]


async def test_success_rotates_atomically_preserves_history_and_drives_fenced_reader(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    before = await snapshot(pool, old)
    started = await begin_authorization(pool, old.context)
    assert await snapshot(pool, old) == before
    assert await load_grant(pool, tenant, old.id) == old
    code = SecretStr("synthetic-code")
    calls = 0

    async def verify(context, actual_code):
        nonlocal calls
        calls += 1
        assert context == old.context and actual_code == code
        assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"
        return verified(context)

    new = await complete_authorization(
        pool,
        tenant,
        started.attempt.id,
        state=started.state,
        authorization_code=code,
        verify=verify,
        encrypt=encrypt,
    )
    assert new.context.credential_generation == old.context.credential_generation + 1
    assert new.id != old.id
    assert (await load_grant(pool, tenant, old.id)).state == "disconnected"
    assert await events(pool, tenant, old.id) == [
        {"revision": 1, "state": "active"},
        {"revision": 2, "state": "disconnected"},
    ]
    assert await load_grant(pool, tenant, new.id) == new
    after = await snapshot(pool, new)
    for column, expected in (
        ("access_token", "synthetic-new-access"),
        ("refresh_token", "synthetic-new-refresh"),
    ):
        assert (
            decrypt(
                after[column],
                credential_aad("channel_credentials", column, str(old.context.binding.record_id)),
            )
            == expected
        )
    assert after["meta"] == before["meta"]
    assert await status(pool, tenant, started.attempt.id) == {
        "status": "completed",
        "grant_id": new.id,
    }
    with pytest.raises(TikTokAuthorizationError):
        await complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=code,
            verify=verify,
            encrypt=encrypt,
        )
    assert calls == 1
    operation = await begin_read(
        pool, tenant, new.id, TikTokReadRequest(kind="detail", order_id="SYNTHETIC-1")
    )
    paths = []

    def handler(request):
        paths.append(request.url.path)
        assert request.headers["x-tts-access-token"] == "synthetic-new-access"
        data = (
            {"shops": [{"id": new.context.binding.shop_id, "cipher": "synthetic-new-cipher"}]}
            if len(paths) == 1
            else {"orders": [order_snapshot()]}
        )
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        resolver = TikTokStoredCredentialResolver(load_app=app, decrypt=decrypt, http_client=http)
        await read_verified_order(pool, tenant, operation.id, resolver)
    assert len(paths) == 2


@pytest.mark.parametrize(
    "failure", ["tenant", "state", "cancelled", "expired", "app", "generation"]
)
async def test_preexchange_failures_never_invoke_verifier_or_change_credentials(
    pool: AsyncConnectionPool, migrated_dsn: str, failure: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    started = await begin_authorization(pool, old.context)
    if failure == "cancelled":
        await cancel_authorization(pool, tenant, started.attempt.id)
        with pytest.raises(TikTokAuthorizationError):
            await cancel_authorization(pool, tenant, started.attempt.id)
    elif failure == "expired":
        # Synthetic fixture only: disable user triggers while manufacturing past timestamps.
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute("ALTER TABLE ecommerce_authorization_attempts DISABLE TRIGGER USER")
            await conn.execute(
                "UPDATE ecommerce_authorization_attempts "
                "SET created_at=clock_timestamp()-interval '2 hours',"
                "expires_at=clock_timestamp()-interval '1 hour' WHERE id=%s",
                (started.attempt.id,),
            )
            await conn.execute("ALTER TABLE ecommerce_authorization_attempts ENABLE TRIGGER USER")
    elif failure == "app":
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await transition_app(
                conn, app_id=old.context.app_id, expected_revision=2, state="disabled"
            )
    elif failure == "generation":
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute(
                "UPDATE channel_credentials SET access_token='v1:synthetic-replacement' "
                "WHERE id=%s",
                (old.context.binding.record_id,),
            )
    before = await snapshot(pool, old)

    async def forbidden(*_):
        pytest.fail("must not exchange")

    with pytest.raises(TikTokAuthorizationError):
        await complete_authorization(
            pool,
            uuid4() if failure == "tenant" else tenant,
            started.attempt.id,
            state=SecretStr("wrong") if failure == "state" else started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=forbidden,
            encrypt=encrypt,
        )
    assert await snapshot(pool, old) == before


@pytest.mark.parametrize(
    "failure",
    [
        "tenant",
        "shop",
        "app_revision",
        "generation",
        "scope",
        "expired",
        "future",
        "error",
        "cancel",
        "timeout",
        "access_crypto",
        "refresh_crypto",
        "binding_crypto",
        "version",
    ],
)
async def test_failed_exchange_or_atomic_persistence_keeps_old_connection_and_burns_attempt(
    pool: AsyncConnectionPool, migrated_dsn: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = await setup(pool, migrated_dsn)
    before = await snapshot(pool, old)
    tenant = old.context.binding.client_id
    started = await begin_authorization(pool, old.context)
    calls = 0
    if failure == "timeout":
        monkeypatch.setattr(connections, "EXCHANGE_TIMEOUT_SECONDS", 0.5)

    async def verify(context, _):
        nonlocal calls
        calls += 1
        if failure == "error":
            raise RuntimeError("synthetic safe exchange failure")
        if failure == "cancel":
            raise asyncio.CancelledError
        if failure == "timeout":
            await asyncio.Event().wait()
        result = verified(context)
        if failure in ("tenant", "shop", "app_revision", "generation"):
            raw = context.model_dump()
            if failure in ("tenant", "shop"):
                raw["binding"]["client_id" if failure == "tenant" else "shop_id"] = (
                    uuid4() if failure == "tenant" else "WRONG"
                )
            else:
                raw["app_revision" if failure == "app_revision" else "credential_generation"] += 1
            result = result.model_copy(update={"context": type(context).model_validate(raw)})
        elif failure == "scope":
            result = result.model_copy(update={"scopes": frozenset()})
        elif failure == "expired":
            result = result.model_copy(
                update={"access_expires_at": datetime.now(UTC) - timedelta(seconds=1)}
            )
        elif failure == "future":
            result = result.model_copy(
                update={"verified_at": datetime.now(UTC) + timedelta(hours=1)}
            )
        return result

    def encrypt_fail(value, aad):
        if (
            (failure == "access_crypto" and b".access_token:" in aad)
            or (failure == "refresh_crypto" and b".refresh_token:" in aad)
            or (failure == "binding_crypto" and b".shop_cipher:" in aad)
        ):
            raise CryptoError("synthetic-private-encryption-detail")
        return "plaintext" if failure == "version" else encrypt(value, aad)

    expected = (
        asyncio.CancelledError
        if failure == "cancel"
        else TimeoutError
        if failure == "timeout"
        else RuntimeError
    )
    with pytest.raises(expected) as caught:
        await complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=verify,
            encrypt=encrypt_fail,
        )
    assert "synthetic-private" not in str(caught.value)
    assert await snapshot(pool, old) == before
    assert await load_grant(pool, tenant, old.id) == old
    assert await events(pool, tenant, old.id) == [{"revision": 1, "state": "active"}]
    assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"
    with pytest.raises(TikTokAuthorizationError):
        await complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=verify,
            encrypt=encrypt,
        )
    assert calls == 1


async def test_concurrent_callbacks_and_reconnect_attempts_have_one_generation_winner(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    first = await begin_authorization(pool, old.context)
    second = await begin_authorization(pool, old.context)
    calls = 0

    async def verify(context, _):
        nonlocal calls
        calls += 1
        return verified(context)

    async def finish(started):
        return await complete_authorization(
            pool,
            old.context.binding.client_id,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=verify,
            encrypt=encrypt,
        )

    results = await asyncio.gather(
        finish(first), finish(first), finish(second), return_exceptions=True
    )
    assert sum(isinstance(result, TikTokGrantState) for result in results) == 1
    assert sum(isinstance(result, TikTokAuthorizationError) for result in results) == 2
    assert calls == 1


async def test_attempt_hash_rls_and_immutable_narrow_contracts(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    started = await begin_authorization(pool, old.context)
    rendered = repr(started) + started.model_dump_json() + verified(old.context).model_dump_json()
    assert started.state.get_secret_value() not in rendered
    assert "synthetic-new-access" not in rendered and "synthetic-new-refresh" not in rendered
    with pytest.raises(ValidationError):
        started.attempt.status = "completed"
    with pytest.raises(ValidationError):
        VerifiedAuthorizationResult.model_validate(
            {**verified(old.context).model_dump(), "meta": {}}
        )
    tenant = old.context.binding.client_id
    async with tx(pool, str(tenant)) as conn:
        row = await (
            await conn.execute(
                "SELECT state_hash FROM ecommerce_authorization_attempts WHERE id=%s",
                (started.attempt.id,),
            )
        ).fetchone()
        assert row is not None
        assert len(row["state_hash"]) == 64
        assert started.state.get_secret_value() not in row["state_hash"]
    async with tx(pool, str(uuid4())) as conn:
        assert (
            await (await conn.execute("SELECT id FROM ecommerce_authorization_attempts")).fetchall()
            == []
        )
    for query in (
        "UPDATE ecommerce_authorization_attempts SET context='{}' WHERE id=%s",
        "DELETE FROM ecommerce_authorization_attempts WHERE id=%s",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with tx(pool, str(tenant)) as conn:
                await conn.execute(query, (started.attempt.id,))
    with pytest.raises(psycopg.errors.CheckViolation):
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute(
                "UPDATE ecommerce_authorization_attempts SET context='{}' WHERE id=%s",
                (started.attempt.id,),
            )
    await cancel_authorization(pool, tenant, started.attempt.id)
    assert await load_grant(pool, tenant, old.id) == old


@pytest.mark.parametrize("change", ["app", "generation"])
async def test_commit_gap_change_burns_attempt_without_exchange(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    started = await begin_authorization(pool, old.context)
    original = connections._fence
    checks = 0

    async def fence(conn, context):
        nonlocal checks
        checks += 1
        if checks == 2:
            async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
                if change == "app":
                    await transition_app(
                        operator, app_id=context.app_id, expected_revision=2, state="disabled"
                    )
                else:
                    await operator.execute(
                        "UPDATE channel_credentials SET access_token='v1:synthetic-rotation' "
                        "WHERE id=%s",
                        (context.binding.record_id,),
                    )
        await original(conn, context)

    monkeypatch.setattr(connections, "_fence", fence)

    async def forbidden(*_):
        pytest.fail("commit-gap change must deny before exchange")

    with pytest.raises(TikTokAuthorizationError):
        await complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=forbidden,
            encrypt=encrypt,
        )
    assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"


async def test_revoked_connection_reauthorization_creates_new_grant_without_refresh_fallback(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    await invalidate_grant(pool, tenant, old.id, expected_revision=1, state="revoked")
    started = await begin_authorization(pool, old.context)

    async def verify(context, _):
        return verified(context).model_copy(update={"refresh_token": None})

    new = await complete_authorization(
        pool,
        tenant,
        started.attempt.id,
        state=started.state,
        authorization_code=SecretStr("synthetic-code"),
        verify=verify,
        encrypt=encrypt,
    )
    assert (await load_grant(pool, tenant, old.id)).state == "revoked"
    assert new.id != old.id and new.state == "active"
    assert (await snapshot(pool, new))["refresh_token"] is None


async def test_cancel_loses_after_exchange_consumption(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    started = await begin_authorization(pool, old.context)
    entered, release = asyncio.Event(), asyncio.Event()

    async def verify(context, _):
        entered.set()
        await release.wait()
        return verified(context)

    task = asyncio.create_task(
        complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=verify,
            encrypt=encrypt,
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(TikTokAuthorizationError, match="authorization_attempt_unavailable"):
            await cancel_authorization(pool, tenant, started.attempt.id)
        release.set()
        assert (await asyncio.wait_for(task, 5)).state == "active"
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_first_connect_attempt_stores_only_state_hash(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    assert await pending_row(pool, context) == {
        "channel": "tiktok_shop",
        "shop_id": None,
        "access_token": None,
        "refresh_token": None,
        "expires_at": None,
        "credential_generation": 1,
        "disconnected_at": None,
    }
    started = await begin_first_authorization(pool, context)
    again = await begin_first_authorization(pool, context)
    state = started.state.get_secret_value()
    assert len(state) >= 43 and state != again.state.get_secret_value()
    assert isinstance(started.attempt.context, TikTokFirstConnectContext)
    assert started.attempt.context == context
    assert started.attempt.status == "pending"
    lifetime = started.attempt.expires_at - started.attempt.created_at
    # Both column defaults read clock_timestamp(), so they differ by microseconds.
    assert abs(lifetime - timedelta(minutes=10)) < timedelta(seconds=1)
    assert state not in repr(started) + started.model_dump_json()
    rows = {row["id"]: row for row in await attempts(pool, tenant)}
    assert set(rows) == {started.attempt.id, again.attempt.id}
    row = rows[started.attempt.id]
    assert row["state_hash"] == hashlib.sha256(state.encode()).hexdigest()
    assert state not in row["context"]
    stored = json.loads(row["context"])
    assert "shop_id" not in stored["binding"]
    assert TikTokFirstConnectContext.model_validate(stored) == context
    # Starting a connect changes nothing on the pending store.
    assert (await pending_row(pool, context))["credential_generation"] == 1
    async with tx(pool, str(uuid4())) as conn:
        assert (
            await (await conn.execute("SELECT id FROM ecommerce_authorization_attempts")).fetchall()
            == []
        )


async def test_pending_store_not_readable_by_dispatch(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    await begin_first_authorization(pool, context)
    # Reads start from a grant, and no grant can exist for a pending store: the readable binding
    # needs a shop, and a guessed shop id does not match the row, so no reconnect attempt, grant
    # or read operation can be built on it.
    with pytest.raises(ValidationError):
        StoreBinding.model_validate(context.binding.model_dump())
    forged = TikTokAuthorizationContext(
        binding=StoreBinding(**context.binding.model_dump(), shop_id=f"SYNTH-SHOP-{uuid4()}"),
        app_id=context.app_id,
        app_revision=context.app_revision,
        credential_generation=context.credential_generation,
    )
    with pytest.raises(TikTokAuthorizationError, match="credential_changed"):
        await begin_authorization(pool, forged)
    now = datetime.now(UTC)
    with pytest.raises(TikTokAuthorizationError, match="grant_create_conflict"):
        await create_verified_grant(
            pool,
            TikTokGrantState(
                id=uuid4(),
                context=forged,
                revision=1,
                state="active",
                source="verified_authorization_exchange",
                scopes=frozenset({"seller.order.info", "seller.authorization.info"}),
                verified_at=now - timedelta(seconds=1),
                expires_at=now + timedelta(hours=1),
            ),
        )
    async with tx(pool, str(tenant)) as conn:
        for table in ("ecommerce_grants", "ecommerce_read_operations"):
            assert (await (await conn.execute(f"SELECT id FROM {table}")).fetchall()) == []
    assert (await pending_row(pool, context))["shop_id"] is None


async def test_pending_store_lock_fails_after_generation_change(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
        await operator.execute(
            """UPDATE channel_credentials SET meta='{"note":"synthetic"}' WHERE id=%s""",
            (context.binding.record_id,),
        )
    assert (await pending_row(pool, context))["credential_generation"] == 2
    with pytest.raises(TikTokAuthorizationError, match="credential_changed"):
        await begin_first_authorization(pool, context)
    assert await attempts(pool, tenant) == []
    current = context.model_copy(update={"credential_generation": 2})
    started = await begin_first_authorization(pool, current)
    assert started.attempt.context == current
    assert [row["id"] for row in await attempts(pool, tenant)] == [started.attempt.id]


@pytest.mark.parametrize("change", ["app", "bound", "disconnected", "channel", "tenant"])
async def test_first_connect_fences_app_and_exact_pending_row(
    pool: AsyncConnectionPool, migrated_dsn: str, change: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant, record_id = context.binding.client_id, context.binding.record_id
    expected = "credential_changed"
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
        if change == "app":
            await transition_app(
                operator, app_id=context.app_id, expected_revision=2, state="disabled"
            )
            expected = "app_revision_unavailable"
        elif change == "bound":
            await operator.execute(
                "UPDATE channel_credentials SET shop_id=%s WHERE id=%s",
                (f"SYNTH-SHOP-{record_id}", record_id),
            )
            context = context.model_copy(update={"credential_generation": 2})
        elif change == "disconnected":
            await operator.execute(
                "UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", (record_id,)
            )
            context = context.model_copy(update={"credential_generation": 2})
        elif change == "channel":
            other = uuid4()
            await operator.execute(
                "INSERT INTO channel_credentials(id,client_id,channel) VALUES (%s,%s,'shopee')",
                (other, tenant),
            )
            context = context.model_copy(
                update={"binding": context.binding.model_copy(update={"record_id": other})}
            )
        else:
            foreign = await pending(pool, migrated_dsn)
            context = context.model_copy(
                update={
                    "binding": context.binding.model_copy(
                        update={"client_id": foreign.binding.client_id}
                    )
                }
            )
    with pytest.raises(TikTokAuthorizationError, match=expected):
        await begin_first_authorization(pool, context)
    assert await attempts(pool, tenant) == []
    assert await attempts(pool, context.binding.client_id) == []


async def test_existing_reconnect_unchanged(pool: AsyncConnectionPool, migrated_dsn: str) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    started = await begin_authorization(pool, old.context)
    assert isinstance(started.attempt.context, TikTokAuthorizationContext)
    assert started.attempt.context == old.context
    lifetime = started.attempt.expires_at - started.attempt.created_at
    # Both column defaults read clock_timestamp(), so they differ by microseconds.
    assert abs(lifetime - timedelta(minutes=10)) < timedelta(seconds=1)

    async def verify(context, _):
        return verified(context)

    new = await complete_authorization(
        pool,
        tenant,
        started.attempt.id,
        state=started.state,
        authorization_code=SecretStr("synthetic-code"),
        verify=verify,
        encrypt=encrypt,
    )
    assert new.context.binding == old.context.binding
    assert new.context.credential_generation == old.context.credential_generation + 1
    assert (await status(pool, tenant, started.attempt.id)) == {
        "status": "completed",
        "grant_id": new.id,
    }
    assert (await load_grant(pool, tenant, old.id)).state == "disconnected"
    assert (await snapshot(pool, new))["credential_generation"] == new.context.credential_generation


async def test_reconnect_completion_refuses_first_connect_attempt(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    started = await begin_first_authorization(pool, context)
    before = await pending_row(pool, context)

    async def forbidden(*_):
        pytest.fail("a first-connect attempt must not reach the reconnect exchange")

    with pytest.raises(TikTokAuthorizationError, match="binding_mismatch"):
        await complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=forbidden,
            encrypt=encrypt,
        )
    # Refused before consumption: the right completer can still use it, nothing else moved.
    assert (await status(pool, tenant, started.attempt.id))["status"] == "pending"
    assert await pending_row(pool, context) == before


async def test_attempt_context_is_told_apart_by_shop_id() -> None:
    now = datetime.now(UTC)
    first = {
        "binding": {
            "client_id": str(uuid4()),
            "record_id": str(uuid4()),
            "platform": "tiktok_shop",
        },
        "app_id": "SYNTH-APP",
        "app_revision": 2,
        "credential_generation": 1,
    }
    reconnect = {**first, "binding": {**first["binding"], "shop_id": "SYNTH-SHOP"}}

    def attempt(context):
        return {
            "id": uuid4(),
            "context": context,
            "status": "pending",
            "created_at": now,
            "expires_at": now + timedelta(minutes=10),
        }

    parsed = AuthorizationAttempt.model_validate(attempt(first)).context
    assert isinstance(parsed, TikTokFirstConnectContext)
    assert AuthorizationAttempt.model_validate(attempt(parsed.model_dump())).context == parsed
    parsed = AuthorizationAttempt.model_validate(attempt(reconnect)).context
    assert isinstance(parsed, TikTokAuthorizationContext)
    assert AuthorizationAttempt.model_validate(attempt(parsed.model_dump())).context == parsed
    for context in (
        # A null or empty shop id is a broken reconnect context, never a first connect.
        {**reconnect, "binding": {**reconnect["binding"], "shop_id": None}},
        {**reconnect, "binding": {**reconnect["binding"], "shop_id": ""}},
        {**reconnect, "binding": {**reconnect["binding"], "platform": "shopee"}},
        {**first, "binding": {**first["binding"], "platform": "shopee"}},
        {**reconnect, "scopes": ["seller.order.info"]},
        {**first, "scopes": ["seller.order.info"]},
        {**first, "binding": "not-a-binding"},
        {key: value for key, value in first.items() if key != "binding"},
        "not-a-context",
    ):
        with pytest.raises(ValidationError):
            AuthorizationAttempt.model_validate(attempt(context))


FIRST_SCOPES = frozenset({"seller.order.info", "seller.authorization.info", *PRODUCT_SCOPES})
FIRST_SECRETS = (
    "SYNTHETIC-AUTHORIZATION-CODE",
    "SYNTHETIC-FIRST-ACCESS",
    "SYNTHETIC-FIRST-REFRESH",
    "SYNTHETIC-FIRST-CIPHER",
    "synthetic-secret",
)


def first_verified(context, shop_id, *, scopes=FIRST_SCOPES):
    now = datetime.now(UTC)
    return VerifiedFirstConnectResult(
        context=context,
        shop_id=shop_id,
        access_token=SecretStr("SYNTHETIC-FIRST-ACCESS"),
        refresh_token=SecretStr("SYNTHETIC-FIRST-REFRESH"),
        shop_cipher=SecretStr("SYNTHETIC-FIRST-CIPHER"),
        access_expires_at=now + timedelta(hours=1),
        grant_expires_at=now + timedelta(hours=2),
        verified_at=now,
        scopes=scopes,
    )


async def finish_first(pool, started, verify, *, client_id=None):
    return await complete_first_authorization(
        pool,
        client_id or started.attempt.context.binding.client_id,
        started.attempt.id,
        state=started.state,
        authorization_code=SecretStr("SYNTHETIC-AUTHORIZATION-CODE"),
        verify=verify,
        encrypt=encrypt,
    )


async def stored(pool, tenant):
    """Grants and shop bindings the tenant can see."""
    async with tx(pool, str(tenant)) as conn:
        grants = await (
            await conn.execute("SELECT id,record_id,shop_id,state FROM ecommerce_grants")
        ).fetchall()
        bindings = await (
            await conn.execute("SELECT grant_id,shop_cipher FROM ecommerce_shop_bindings")
        ).fetchall()
    return grants, bindings


def mock_tiktok(monkeypatch, shops, *, scopes=FIRST_SCOPES):
    """The real verifiers' two provider calls, mocked; returns the list of calls seen."""
    seen: list[str] = []

    def token_handler(request: httpx.Request) -> httpx.Response:
        seen.append("exchange")
        now = datetime.now(UTC)
        payload = {
            "code": 0,
            "data": {
                "access_token": "SYNTHETIC-FIRST-ACCESS",
                "refresh_token": "SYNTHETIC-FIRST-REFRESH",
                "access_token_expire_in": int((now + timedelta(hours=1)).timestamp()),
                "refresh_token_expire_in": int((now + timedelta(days=30)).timestamp()),
                "user_type": 0,
                "granted_scopes": sorted(scopes),
            },
        }
        return httpx.Response(200, stream=_BytesStream(json.dumps(payload).encode()))

    def shop_handler(request: httpx.Request) -> httpx.Response:
        seen.append("shops")
        assert request.headers["x-tts-access-token"] == "SYNTHETIC-FIRST-ACCESS"
        assert "shop_cipher" not in request.url.params
        payload = {"code": 0, "data": {"shops": [{"id": i, "cipher": c} for i, c in shops]}}
        return httpx.Response(200, stream=_BytesStream(json.dumps(payload).encode()))

    monkeypatch.setattr(
        token_exchange, "_new_transport", lambda: httpx.MockTransport(token_handler)
    )
    monkeypatch.setattr(
        authorization_verifier, "_new_shop_transport", lambda: httpx.MockTransport(shop_handler)
    )
    return seen


async def test_first_connect_binds_the_single_shop(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant, record_id = context.binding.client_id, context.binding.record_id
    shop_id = f"SYNTH-SHOP-{record_id}"
    seen = mock_tiktok(monkeypatch, [(shop_id, "SYNTHETIC-FIRST-CIPHER")])
    started = await begin_first_authorization(pool, context)
    grant = await finish_first(pool, started, TikTokFirstConnectVerifier(load_app=app))

    assert seen == ["exchange", "shops"]
    assert grant.context == TikTokAuthorizationContext(
        binding=StoreBinding(
            client_id=tenant, record_id=record_id, platform="tiktok_shop", shop_id=shop_id
        ),
        app_id=context.app_id,
        app_revision=context.app_revision,
        credential_generation=context.credential_generation + 1,
    )
    assert grant.state == "active" and grant.revision == 1
    assert grant.source == "verified_authorization_exchange"
    assert grant.scopes == FIRST_SCOPES
    assert await load_grant(pool, tenant, grant.id) == grant
    row = await pending_row(pool, context)
    assert row["shop_id"] == shop_id
    assert row["credential_generation"] == 2
    assert row["disconnected_at"] is None and row["expires_at"] is not None
    for column, expected in (
        ("access_token", "SYNTHETIC-FIRST-ACCESS"),
        ("refresh_token", "SYNTHETIC-FIRST-REFRESH"),
    ):
        assert row[column].startswith("v1:")
        aad = credential_aad("channel_credentials", column, str(record_id))
        assert decrypt(row[column], aad) == expected
    grants, bindings = await stored(pool, tenant)
    assert [g["id"] for g in grants] == [grant.id]
    assert [b["grant_id"] for b in bindings] == [grant.id]
    aad = credential_aad("ecommerce_shop_bindings", "shop_cipher", str(grant.id))
    assert decrypt(bindings[0]["shop_cipher"], aad) == "SYNTHETIC-FIRST-CIPHER"
    assert await status(pool, tenant, started.attempt.id) == {
        "status": "completed",
        "grant_id": grant.id,
    }
    # The store is now readable through its grant, like any reconnected store.
    operation = await begin_read(
        pool, tenant, grant.id, TikTokReadRequest(kind="detail", order_id="SYNTHETIC-1")
    )
    assert operation.context == grant.context
    # A replay is refused before any exchange (the store is no longer pending).
    with pytest.raises(TikTokAuthorizationError, match="credential_changed"):
        await finish_first(pool, started, TikTokFirstConnectVerifier(load_app=app))
    assert seen == ["exchange", "shops"]


async def _first_connect_fails_and_stores_nothing(pool, migrated_dsn, monkeypatch, shops, code):
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    seen = mock_tiktok(monkeypatch, shops)
    started = await begin_first_authorization(pool, context)
    before = await pending_row(pool, context)
    with pytest.raises(TikTokAuthorizationError, match=code) as caught:
        await finish_first(pool, started, TikTokFirstConnectVerifier(load_app=app))
    assert "SYNTH-SHOP" not in str(caught.value)
    assert await pending_row(pool, context) == before
    assert before["shop_id"] is None
    assert await stored(pool, tenant) == ([], [])
    assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"
    with pytest.raises(TikTokAuthorizationError, match="authorization_attempt_unavailable"):
        await finish_first(pool, started, TikTokFirstConnectVerifier(load_app=app))
    assert seen == ["exchange", "shops"]


async def test_multiple_shops_fails_and_stores_nothing(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    shops = [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-ONE"), (f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-TWO")]
    await _first_connect_fails_and_stores_nothing(
        pool, migrated_dsn, monkeypatch, shops, "multiple_shops"
    )


async def test_no_authorized_shop_fails(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _first_connect_fails_and_stores_nothing(
        pool, migrated_dsn, monkeypatch, [], "no_authorized_shop"
    )


@pytest.mark.parametrize("where", ["verifier", "completer"])
async def test_missing_product_scope_fails_insufficient_scope(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    shop_id = f"SYNTH-SHOP-{context.binding.record_id}"
    order_and_shop = frozenset({"seller.order.info", "seller.authorization.info"})
    seen: list[str] = []

    async def forgetful(actual, _):
        # A verifier that forgets the product check is still caught by the completer.
        seen.append("verify")
        return first_verified(actual, shop_id, scopes=order_and_shop)

    verify = forgetful
    if where == "verifier":
        seen = mock_tiktok(
            monkeypatch, [(shop_id, "SYNTHETIC-FIRST-CIPHER")], scopes=order_and_shop
        )
        verify = TikTokFirstConnectVerifier(load_app=app)
    started = await begin_first_authorization(pool, context)
    before = await pending_row(pool, context)
    with pytest.raises(TikTokAuthorizationError, match="insufficient_scope"):
        await finish_first(pool, started, verify)
    assert seen == (["exchange"] if where == "verifier" else ["verify"])
    assert await pending_row(pool, context) == before
    assert await stored(pool, tenant) == ([], [])
    assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"


@pytest.mark.parametrize(
    "holder",
    ["other_tenant_active", "same_tenant_active", "same_tenant_disconnected", "other_tenant_gone"],
)
async def test_shop_active_for_another_client_fails_shop_in_use_without_leaking(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    caplog: pytest.LogCaptureFixture,
    holder: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    if holder.startswith("other_tenant"):
        other = await setup(pool, migrated_dsn)
        holder_grant = other.id
        holder_client = other.context.binding.client_id
        holder_record = other.context.binding.record_id
        shop_id = other.context.binding.shop_id
    else:
        holder_grant, holder_client, holder_record = None, tenant, uuid4()
        shop_id = f"SYNTH-SHOP-{holder_record}"
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
            await operator.execute(
                "INSERT INTO channel_credentials(id,client_id,channel,shop_id) "
                "VALUES (%s,%s,'tiktok_shop',%s)",
                (holder_record, tenant, shop_id),
            )
    if holder.endswith(("_disconnected", "_gone")):
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
            await operator.execute(
                "UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s",
                (holder_record,),
            )
    async with tx(pool, str(holder_client)) as conn:
        holder_before = await (
            await conn.execute("SELECT * FROM channel_credentials WHERE id=%s", (holder_record,))
        ).fetchone()
    started = await begin_first_authorization(pool, context)
    before = await pending_row(pool, context)

    async def verify(actual, _):
        return first_verified(actual, shop_id)

    if holder == "other_tenant_gone":
        # A shop another client has disconnected is free to connect.
        grant = await finish_first(pool, started, verify)
        assert grant.context.binding.shop_id == shop_id
        assert (await pending_row(pool, context))["shop_id"] == shop_id
    else:
        with pytest.raises(TikTokAuthorizationError, match="shop_in_use") as caught:
            await finish_first(pool, started, verify)
        leaked = str(caught.value) + repr(caught.value) + caplog.text
        for private in (str(holder_client), str(holder_record), shop_id, *FIRST_SECRETS):
            assert private not in leaked
        assert caught.value.__cause__ is None and caught.value.__suppress_context__
        assert await pending_row(pool, context) == before
        # A same-tenant holder row was inserted without a grant, so nothing is stored either way.
        assert await stored(pool, tenant) == ([], [])
        assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"
    async with tx(pool, str(holder_client)) as conn:
        holder_after = await (
            await conn.execute("SELECT * FROM channel_credentials WHERE id=%s", (holder_record,))
        ).fetchone()
    assert holder_after == holder_before
    if holder == "other_tenant_active":
        assert holder_grant is not None
        assert (await load_grant(pool, holder_client, holder_grant)).state == "active"


async def test_concurrent_first_connects_of_one_shop_have_one_winner(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    first, second = await pending(pool, migrated_dsn), await pending(pool, migrated_dsn)
    shop_id = f"SYNTH-SHOP-{first.binding.record_id}"

    async def verify(actual, _):
        return first_verified(actual, shop_id)

    started = [await begin_first_authorization(pool, c) for c in (first, second)]
    results = await asyncio.gather(
        *(finish_first(pool, s, verify) for s in started), return_exceptions=True
    )
    assert sum(isinstance(r, TikTokGrantState) for r in results) == 1
    errors = [r for r in results if isinstance(r, TikTokAuthorizationError)]
    assert [e.code for e in errors] == ["shop_in_use"]
    bound = [(await pending_row(pool, c))["shop_id"] for c in (first, second)]
    assert sorted(bound, key=str) == sorted([shop_id, None], key=str)


async def test_reconnect_shop_mismatch_changes_nothing(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    seen = mock_tiktok(monkeypatch, [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-OTHER-CIPHER")])
    before = await snapshot(pool, old)
    before_events = await events(pool, tenant, old.id)
    before_stored = await stored(pool, tenant)
    started = await begin_authorization(pool, old.context)
    # The reconnect path keeps its existing safe code for a fresh shop that is not the stored one.
    with pytest.raises(TikTokAuthorizationError, match="authorized_shop_verification_failed"):
        await complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("SYNTHETIC-AUTHORIZATION-CODE"),
            verify=TikTokAuthorizationVerifier(load_app=app),
            encrypt=encrypt,
        )
    assert seen == ["exchange", "shops"]
    assert await snapshot(pool, old) == before
    assert await load_grant(pool, tenant, old.id) == old
    assert await events(pool, tenant, old.id) == before_events
    assert await stored(pool, tenant) == before_stored
    assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"


async def test_first_connect_logs_no_token_or_code(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    context = await pending(pool, migrated_dsn)
    shop_id = f"SYNTH-SHOP-{context.binding.record_id}"
    mock_tiktok(monkeypatch, [(shop_id, "SYNTHETIC-FIRST-CIPHER")])
    started = await begin_first_authorization(pool, context)
    grant = await finish_first(pool, started, TikTokFirstConnectVerifier(load_app=app))
    failing = await pending(pool, migrated_dsn)
    mock_tiktok(
        monkeypatch,
        [(f"SYNTH-SHOP-{uuid4()}", "SYNTHETIC-FIRST-CIPHER"), (shop_id, "SYNTHETIC-FIRST-CIPHER")],
    )
    failed = await begin_first_authorization(pool, failing)
    with pytest.raises(TikTokAuthorizationError, match="multiple_shops") as caught:
        await finish_first(pool, failed, TikTokFirstConnectVerifier(load_app=app))
    rendered = caplog.text + str(caught.value) + repr(grant) + grant.model_dump_json()
    for secret in (
        *FIRST_SECRETS,
        started.state.get_secret_value(),
        failed.state.get_secret_value(),
    ):
        assert secret not in rendered


async def test_first_connect_completion_refuses_reconnect_attempt_before_consuming(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant = old.context.binding.client_id
    started = await begin_authorization(pool, old.context)
    before = await snapshot(pool, old)

    async def forbidden(*_):
        pytest.fail("a reconnect attempt must not reach the first-connect exchange")

    with pytest.raises(TikTokAuthorizationError, match="binding_mismatch"):
        await finish_first(pool, started, forbidden)
    assert (await status(pool, tenant, started.attempt.id))["status"] == "pending"
    assert await snapshot(pool, old) == before

    async def verify(actual, _):
        return verified(actual)

    # The right completer can still use it.
    await complete_authorization(
        pool,
        tenant,
        started.attempt.id,
        state=started.state,
        authorization_code=SecretStr("synthetic-code"),
        verify=verify,
        encrypt=encrypt,
    )


@pytest.mark.parametrize(
    "failure", ["tenant", "state", "generation", "app", "gap_generation", "gap_app"]
)
async def test_first_connect_preexchange_failures_never_verify_or_bind(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant, record_id = context.binding.client_id, context.binding.record_id
    started = await begin_first_authorization(pool, context)

    async def change(operator):
        if failure.endswith("app"):
            await transition_app(
                operator, app_id=context.app_id, expected_revision=2, state="disabled"
            )
        else:
            await operator.execute(
                """UPDATE channel_credentials SET meta='{"note":"synthetic"}' WHERE id=%s""",
                (record_id,),
            )

    if failure in ("generation", "app"):
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
            await change(operator)
    elif failure.startswith("gap_"):
        original, checks = connections._fence_pending, 0

        async def fence(conn, actual):
            nonlocal checks
            checks += 1
            if checks == 2:
                async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
                    await change(operator)
            await original(conn, actual)

        monkeypatch.setattr(connections, "_fence_pending", fence)

    async def forbidden(*_):
        pytest.fail("must not exchange")

    with pytest.raises(TikTokAuthorizationError):
        await complete_first_authorization(
            pool,
            uuid4() if failure == "tenant" else tenant,
            started.attempt.id,
            state=SecretStr("wrong") if failure == "state" else started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=forbidden,
            encrypt=encrypt,
        )
    assert (await pending_row(pool, context))["shop_id"] is None
    assert await stored(pool, tenant) == ([], [])
    expected = "consumed" if failure.startswith("gap_") else "pending"
    assert (await status(pool, tenant, started.attempt.id))["status"] == expected


@pytest.mark.parametrize("failure", ["context", "shop", "future", "expired", "binding_crypto"])
async def test_first_connect_bad_verifier_result_or_persistence_binds_nothing(
    pool: AsyncConnectionPool, migrated_dsn: str, failure: str
) -> None:
    context = await pending(pool, migrated_dsn)
    tenant = context.binding.client_id
    shop_id = f"SYNTH-SHOP-{context.binding.record_id}"
    started = await begin_first_authorization(pool, context)
    before = await pending_row(pool, context)

    async def verify(actual, _):
        result = first_verified(actual, shop_id)
        now = datetime.now(UTC)
        if failure == "context":
            other = actual.model_copy(update={"credential_generation": 2})
            return result.model_copy(update={"context": other})
        if failure == "shop":
            return result.model_copy(update={"shop_id": ""})
        if failure == "future":
            return result.model_copy(update={"verified_at": now + timedelta(hours=1)})
        if failure == "expired":
            return result.model_copy(update={"access_expires_at": now - timedelta(seconds=1)})
        return result

    def encrypt_fail(value, aad):
        if failure == "binding_crypto" and b".shop_cipher:" in aad:
            raise CryptoError("synthetic-private-encryption-detail")
        return encrypt(value, aad)

    with pytest.raises(TikTokAuthorizationError) as caught:
        await complete_first_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=verify,
            encrypt=encrypt_fail,
        )
    assert "synthetic-private" not in str(caught.value)
    assert await pending_row(pool, context) == before
    assert await stored(pool, tenant) == ([], [])
    assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"


async def test_reconnect_of_disconnected_store_clears_disconnected_at(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant, record_id = old.context.binding.client_id, old.context.binding.record_id
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
        await operator.execute(
            "UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", (record_id,)
        )
    assert (await load_grant(pool, tenant, old.id)).state == "disconnected"
    context = old.context.model_copy(
        update={"credential_generation": old.context.credential_generation + 1}
    )
    started = await begin_authorization(pool, context)

    async def verify(actual, _):
        return verified(actual)

    new = await complete_authorization(
        pool,
        tenant,
        started.attempt.id,
        state=started.state,
        authorization_code=SecretStr("synthetic-code"),
        verify=verify,
        encrypt=encrypt,
    )
    # One UPDATE replaces the tokens and clears disconnected_at: the generation moves once.
    assert new.context.credential_generation == context.credential_generation + 1
    async with tx(pool, str(tenant)) as conn:
        row = await (
            await conn.execute(
                "SELECT disconnected_at,credential_generation FROM channel_credentials WHERE id=%s",
                (record_id,),
            )
        ).fetchone()
    assert row == {
        "disconnected_at": None,
        "credential_generation": new.context.credential_generation,
    }
    assert (await load_grant(pool, tenant, new.id)).state == "active"
    assert await status(pool, tenant, started.attempt.id) == {
        "status": "completed",
        "grant_id": new.id,
    }


async def test_reconnect_of_disconnected_store_whose_shop_moved_fails_shop_in_use(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    old = await setup(pool, migrated_dsn)
    tenant, record_id = old.context.binding.client_id, old.context.binding.record_id
    shop_id = old.context.binding.shop_id
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as operator:
        await operator.execute(
            "UPDATE channel_credentials SET disconnected_at=now() WHERE id=%s", (record_id,)
        )
    newcomer = await pending(pool, migrated_dsn)
    taken = await begin_first_authorization(pool, newcomer)

    async def verify_first(actual, _):
        return first_verified(actual, shop_id)

    await finish_first(pool, taken, verify_first)
    context = old.context.model_copy(
        update={"credential_generation": old.context.credential_generation + 1}
    )
    started = await begin_authorization(pool, context)
    before = await snapshot(pool, old)

    async def verify(actual, _):
        return verified(actual)

    with pytest.raises(TikTokAuthorizationError, match="shop_in_use") as caught:
        await complete_authorization(
            pool,
            tenant,
            started.attempt.id,
            state=started.state,
            authorization_code=SecretStr("synthetic-code"),
            verify=verify,
            encrypt=encrypt,
        )
    assert str(newcomer.binding.client_id) not in str(caught.value)
    assert caught.value.__suppress_context__
    assert await snapshot(pool, old) == before
    assert (await status(pool, tenant, started.attempt.id))["status"] == "consumed"


async def test_create_pending_store_refuses_another_tenant(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    context = await pending(pool, migrated_dsn)
    other = await pending(pool, migrated_dsn)
    with pytest.raises(TikTokAuthorizationError, match="binding_mismatch"):
        async with tx(pool, str(context.binding.client_id)) as conn:
            await create_pending_store(conn, other.binding.client_id)
    async with tx(pool, str(other.binding.client_id)) as conn:
        rows = await (await conn.execute("SELECT id FROM channel_credentials")).fetchall()
    assert rows == [{"id": other.binding.record_id}]
