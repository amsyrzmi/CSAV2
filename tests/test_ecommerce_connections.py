"""Offline authorization attempts with synthetic exchange, crypto and disposable DB only."""

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import psycopg
import pytest
from csa.agents.ecommerce import authorization_verifier, connections, token_exchange
from csa.agents.ecommerce.apps import transition_app
from csa.agents.ecommerce.authorization import (
    TikTokAuthorizationError,
    TikTokGrantState,
)
from csa.agents.ecommerce.authorization_verifier import TikTokAuthorizationVerifier
from csa.agents.ecommerce.connections import (
    VerifiedAuthorizationResult,
    begin_authorization,
    cancel_authorization,
    complete_authorization,
)
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.grants import create_verified_grant, invalidate_grant, load_grant
from csa.agents.ecommerce.reader import read_verified_order
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.storage import (
    TikTokStoredCredentialResolver,
    create_verified_shop_binding,
)
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
            {"shops": [{"id": "SYNTH-SHOP", "cipher": "synthetic-new-cipher"}]}
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
