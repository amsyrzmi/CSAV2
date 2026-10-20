"""Synthetic exchange evidence on disposable databases; never use environment keys."""

import asyncio
import base64
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import psycopg
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from csa.agents.ecommerce.authorization import TikTokAuthorizationContext, TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.grants import create_verified_grant, invalidate_grant
from csa.agents.ecommerce.reader import read_verified_order
from csa.agents.ecommerce.resolver import TikTokAppMaterial
from csa.agents.ecommerce.storage import (
    TikTokStoredCredentialResolver,
    create_verified_shop_binding,
)
from csa.agents.ecommerce.tiktok_models import TikTokOrderSnapshot
from csa.core import db
from csa.core.crypto import CryptoError, credential_aad
from csa.core.tenancy import tx
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr

from tests.conftest import requires_docker
from tests.test_ecommerce_grants import seed
from tests.test_tiktok_snapshots import order_snapshot

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]

AES = AESGCM(b"\x09" * 32)


def encrypt(value: str, aad: bytes) -> str:
    nonce = os.urandom(12)
    return (
        "v1:" + base64.urlsafe_b64encode(nonce + AES.encrypt(nonce, value.encode(), aad)).decode()
    )


def decrypt(value: str, aad: bytes) -> str:
    blob = base64.urlsafe_b64decode(value[3:])
    try:
        return AES.decrypt(blob[:12], blob[12:], aad).decode()
    except InvalidTag:
        raise CryptoError("synthetic-private-authentication-detail") from None


async def prepare(owner: str, *, expired: bool = False, missing_expiry: bool = False):
    grant = await seed(owner)
    binding = grant.context.binding
    encrypted = encrypt(
        "synthetic-token",
        credential_aad("channel_credentials", "access_token", str(binding.record_id)),
    )
    async with await psycopg.AsyncConnection.connect(owner) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET access_token=%s, expires_at=%s, "
            'meta=\'{"shop_cipher":"untrusted-meta"}\' WHERE id=%s',
            (
                encrypted,
                None
                if missing_expiry
                else datetime.now(UTC) + timedelta(hours=-1 if expired else 1),
                binding.record_id,
            ),
        )
    return grant.model_copy(
        update={"context": grant.context.model_copy(update={"credential_generation": 2})}
    )


async def app(app_id: str):
    return TikTokAppMaterial(
        app_id=app_id,
        revision=2,
        app_key=SecretStr("synthetic-key"),
        app_secret=SecretStr("synthetic-secret"),
        enabled=True,
    )


async def test_stored_resolver_reads_once_using_single_connection_pool(
    app_dsn: str, migrated_dsn: str
) -> None:
    pool = db.build_pool(app_dsn, min_size=1, max_size=1)
    await pool.open(wait=True)
    try:
        grant = await prepare(migrated_dsn)
        await create_verified_grant(pool, grant)
        await create_verified_shop_binding(
            pool, grant, SecretStr("synthetic-cipher"), encrypt=encrypt
        )
        tenant = grant.context.binding.client_id
        operation = await begin_read(
            pool, tenant, grant.id, TikTokReadRequest(kind="detail", order_id="SYNTHETIC-1")
        )
        calls = []

        def handler(request: httpx.Request):
            calls.append(request.url.path)
            assert request.headers["x-tts-access-token"] == "synthetic-token"
            if len(calls) == 1:
                data = {"shops": [{"id": "SYNTH-SHOP", "cipher": "synthetic-cipher"}]}
            else:
                assert request.url.params["shop_cipher"] == "synthetic-cipher"
                data = {"orders": [order_snapshot()]}
            return httpx.Response(200, json={"code": 0, "data": data})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            resolver = TikTokStoredCredentialResolver(
                load_app=app, decrypt=decrypt, http_client=http
            )
            async with asyncio.timeout(5):
                result = await read_verified_order(pool, tenant, operation.id, resolver)
            assert isinstance(result, TikTokOrderSnapshot)
            assert result.id == "SYNTHETIC-1"
            assert calls == ["/authorization/202309/shops", "/order/202309/orders"]
            with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
                await read_verified_order(pool, tenant, operation.id, resolver)
            assert len(calls) == 2
        async with tx(pool, str(tenant)) as conn:
            row = await (
                await conn.execute("SELECT shop_cipher FROM ecommerce_shop_bindings")
            ).fetchone()
            assert row is not None
            assert row["shop_cipher"].startswith("v1:")
            assert "synthetic-cipher" not in row["shop_cipher"]
    finally:
        await pool.close()


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "tenant",
        "record",
        "shop",
        "app",
        "generation",
        "expired",
        "missing_expiry",
        "revoked",
        "rotated",
    ],
)
async def test_stored_loader_rejects_missing_or_wrong_binding_before_decrypt(
    pool: AsyncConnectionPool, migrated_dsn: str, failure: str
) -> None:
    grant = await prepare(
        migrated_dsn, expired=failure == "expired", missing_expiry=failure == "missing_expiry"
    )
    await create_verified_grant(pool, grant)
    tenant = grant.context.binding.client_id
    if failure != "missing":
        await create_verified_shop_binding(
            pool, grant, SecretStr("synthetic-cipher"), encrypt=encrypt
        )
    raw = grant.context.model_dump()
    if failure in ("tenant", "record", "shop"):
        key = {"tenant": "client_id", "record": "record_id", "shop": "shop_id"}[failure]
        raw["binding"][key] = "WRONG" if failure == "shop" else uuid4()
    elif failure == "app":
        raw["app_id"] = "WRONG"
    elif failure == "generation":
        raw["credential_generation"] += 1
    elif failure == "revoked":
        await invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="revoked")
    elif failure == "rotated":
        async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
            await conn.execute(
                "UPDATE channel_credentials SET access_token='v1:replacement' WHERE id=%s",
                (grant.context.binding.record_id,),
            )
    context = TikTokAuthorizationContext.model_validate(raw)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("must not send HTTP"))
    ) as http:
        resolver = TikTokStoredCredentialResolver(
            load_app=app, decrypt=lambda *_: pytest.fail("must not decrypt"), http_client=http
        )
        async with tx(pool, str(context.binding.client_id)) as conn:
            with pytest.raises(
                TikTokAuthorizationError,
                match="app_revision_unavailable"
                if failure == "app"
                else "credential_binding_unavailable",
            ):
                await resolver(context, conn)


@pytest.mark.parametrize("failure", ["aad", "tamper", "empty", "crypto"])
async def test_stored_cipher_crypto_failures_are_safe(
    pool: AsyncConnectionPool, migrated_dsn: str, failure: str
) -> None:
    grant = await prepare(migrated_dsn)
    await create_verified_grant(pool, grant)

    def encrypt_bad(value: str, aad: bytes):
        if failure == "empty":
            return encrypt("", aad)
        value = encrypt(value, b"wrong-row" if failure == "aad" else aad)
        if failure == "tamper":
            blob = bytearray(base64.urlsafe_b64decode(value[3:]))
            blob[-1] ^= 1
            value = "v1:" + base64.urlsafe_b64encode(blob).decode()
        return value

    await create_verified_shop_binding(
        pool, grant, SecretStr("synthetic-cipher"), encrypt=encrypt_bad
    )

    def decrypt_bad(value: str, aad: bytes):
        if failure == "crypto":
            raise CryptoError("synthetic-private")
        return decrypt(value, aad)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("must not send HTTP"))
    ) as http:
        resolver = TikTokStoredCredentialResolver(
            load_app=app, decrypt=decrypt_bad, http_client=http
        )
        async with tx(pool, str(grant.context.binding.client_id)) as conn:
            with pytest.raises(TikTokAuthorizationError) as caught:
                await resolver(grant.context, conn)
            assert caught.value.code == (
                "empty_shop_cipher" if failure == "empty" else "shop_cipher_decryption_failed"
            )
            assert "synthetic" not in str(caught.value)
            if failure != "empty":
                assert caught.value.__suppress_context__


async def test_binding_rls_immutability_and_conflict(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    grant, foreign = await prepare(migrated_dsn), await prepare(migrated_dsn)
    for item in (grant, foreign):
        await create_verified_grant(pool, item)
        await create_verified_shop_binding(
            pool, item, SecretStr("synthetic-cipher"), encrypt=encrypt
        )
    with pytest.raises(TikTokAuthorizationError, match="shop_binding_conflict"):
        await create_verified_shop_binding(pool, grant, SecretStr("replacement"), encrypt=encrypt)
    tenant = grant.context.binding.client_id
    async with tx(pool, str(tenant)) as conn:
        assert await (
            await conn.execute("SELECT grant_id FROM ecommerce_shop_bindings")
        ).fetchall() == [{"grant_id": grant.id}]
    async with pool.connection() as conn:
        assert (
            await (await conn.execute("SELECT grant_id FROM ecommerce_shop_bindings")).fetchall()
            == []
        )
    for statement in (
        "UPDATE ecommerce_shop_bindings SET shop_cipher='v1:replacement'",
        "DELETE FROM ecommerce_shop_bindings",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with tx(pool, str(tenant)) as conn:
                await conn.execute(statement)
        with pytest.raises(psycopg.errors.CheckViolation):
            async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
                await conn.execute(statement)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute(
                "INSERT INTO ecommerce_shop_bindings VALUES (%s,%s,'v1:synthetic')",
                (foreign.context.binding.client_id, uuid4()),
            )
    unbound_foreign = await prepare(migrated_dsn)
    await create_verified_grant(pool, unbound_foreign)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        async with tx(pool, str(tenant)) as conn:
            await conn.execute(
                "INSERT INTO ecommerce_shop_bindings VALUES (%s,%s,'v1:synthetic')",
                (tenant, unbound_foreign.id),
            )


@pytest.mark.parametrize(
    "failure",
    ["missing_grant", "context", "revoked", "encryption", "crypto", "empty", "generation"],
)
async def test_writer_requires_exact_active_grant_and_safe_encryption(
    pool: AsyncConnectionPool, migrated_dsn: str, failure: str
) -> None:
    grant = await prepare(migrated_dsn)
    if failure != "missing_grant":
        await create_verified_grant(pool, grant)
    if failure == "revoked":
        await invalidate_grant(
            pool, grant.context.binding.client_id, grant.id, expected_revision=1, state="revoked"
        )
    elif failure == "context":
        grant = grant.model_copy(
            update={"context": grant.context.model_copy(update={"app_id": "OTHER"})}
        )
    elif failure == "generation":
        grant = grant.model_copy(
            update={"context": grant.context.model_copy(update={"credential_generation": 99})}
        )

    def encrypt_failure(value: str, aad: bytes):
        if failure == "crypto":
            raise CryptoError("synthetic-private-encryption-detail")
        if failure == "encryption":
            return "plaintext"
        return encrypt(value, aad)

    with pytest.raises(TikTokAuthorizationError) as caught:
        await create_verified_shop_binding(
            pool,
            grant,
            SecretStr("" if failure == "empty" else "synthetic-cipher"),
            encrypt=encrypt_failure,
        )
    assert "synthetic" not in str(caught.value)
    async with tx(pool, str(grant.context.binding.client_id)) as conn:
        assert (
            await (await conn.execute("SELECT grant_id FROM ecommerce_shop_bindings")).fetchall()
            == []
        )


@pytest.mark.parametrize("failure", ["missing", "disabled", "mismatch"])
async def test_external_app_binding_is_checked_before_material_query(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    grant = await prepare(migrated_dsn)

    async def unavailable(app_id: str):
        if failure == "missing":
            return None
        material = await app(app_id)
        return material.model_copy(
            update={"enabled": False} if failure == "disabled" else {"app_id": "OTHER"}
        )

    original = db.q.fetch_one

    async def forbidden(path, *args, **kwargs):
        if path == "ecommerce/get_credential_material.sql":
            pytest.fail("must validate external registry before any credential query")
        return await original(path, *args, **kwargs)

    monkeypatch.setattr(db.q, "fetch_one", forbidden)
    async with httpx.AsyncClient() as http:
        resolver = TikTokStoredCredentialResolver(
            load_app=unavailable,
            decrypt=lambda *_: pytest.fail("must not decrypt"),
            http_client=http,
        )
        async with tx(pool, str(grant.context.binding.client_id)) as conn:
            with pytest.raises(TikTokAuthorizationError, match="app_binding_unavailable"):
                await resolver(grant.context, conn)
