"""Synthetic resolver fixtures; no environment key, stored credential or real HTTP."""

import base64
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from csa.agents.ecommerce.authorization import TikTokAuthorizationContext, TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.grants import create_verified_grant
from csa.agents.ecommerce.reader import read_verified_order
from csa.agents.ecommerce.resolver import (
    TikTokAppMaterial,
    TikTokCredentialMaterial,
    TikTokCredentialResolver,
)
from csa.agents.ecommerce.stores import StoreBinding
from csa.core.crypto import CryptoError, credential_aad
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr, ValidationError

from tests.conftest import requires_docker
from tests.test_ecommerce_grants import seed
from tests.test_tiktok_snapshots import order_snapshot

pytest_plugins = ["tests.test_ecommerce_grants"]

NOW = datetime(2026, 10, 18, tzinfo=UTC)


def fixtures():
    context = TikTokAuthorizationContext(
        binding=StoreBinding(
            client_id=uuid4(),
            record_id=uuid4(),
            platform="tiktok_shop",
            shop_id="SYNTH-SHOP",
        ),
        app_id="SYNTH-APP-ID",
        credential_generation=7,
    )
    app = TikTokAppMaterial(
        app_id=context.app_id,
        revision=context.app_revision,
        app_key=SecretStr("synthetic-key"),
        app_secret=SecretStr("synthetic-secret"),
        enabled=True,
    )
    aes = AESGCM(b"\x07" * 32)
    aad = credential_aad("channel_credentials", "access_token", str(context.binding.record_id))
    nonce = os.urandom(12)
    ciphertext = (
        "v1:"
        + base64.urlsafe_b64encode(nonce + aes.encrypt(nonce, b"synthetic-token", aad)).decode()
    )
    material = TikTokCredentialMaterial(
        context=context,
        expires_at=NOW + timedelta(hours=1),
        access_token_ciphertext=SecretStr(ciphertext),
        shop_cipher=SecretStr("synthetic-cipher"),
    )
    return context, app, material, aes, aad


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        None,
        "app_missing",
        "app_disabled",
        "app_mismatch",
        "app_revision",
        "credential_missing",
        "tenant",
        "record",
        "shop",
        "generation",
        "app",
        "expired",
        "boundary",
        "version",
        "aad",
        "tamper",
        "empty",
        "clock",
    ],
)
async def test_resolver_exact_binding_expiry_crypto_and_safe_failures(failure: str | None) -> None:
    context, app, material, aes, aad = fixtures()
    decrypt_calls = 0
    http_calls = 0
    if failure in ("tenant", "record", "shop", "generation", "app"):
        raw = context.model_dump()
        if failure == "generation":
            raw["credential_generation"] += 1
        elif failure == "app":
            raw["app_id"] = "OTHER"
        else:
            key = {"tenant": "client_id", "record": "record_id", "shop": "shop_id"}[failure]
            raw["binding"][key] = "OTHER" if failure == "shop" else uuid4()
        material = TikTokCredentialMaterial.model_validate(
            {
                **material.model_dump(),
                "context": raw,
            }
        )
    elif failure in ("expired", "boundary"):
        material = material.model_copy(
            update={
                "expires_at": NOW if failure == "boundary" else NOW - timedelta(seconds=1),
            }
        )
    elif failure == "version":
        material = material.model_copy(update={"access_token_ciphertext": SecretStr("plaintext")})

    async def load_app(app_id: str):
        assert app_id == context.app_id
        if failure == "app_missing":
            return None
        if failure == "app_disabled":
            return app.model_copy(update={"enabled": False})
        if failure == "app_mismatch":
            return app.model_copy(update={"app_id": "OTHER"})
        if failure == "app_revision":
            return app.model_copy(update={"revision": app.revision + 1})
        return app

    async def load_credential(requested: TikTokAuthorizationContext):
        assert requested == context
        return None if failure == "credential_missing" else material

    def decrypt(ciphertext: str, actual_aad: bytes) -> str:
        nonlocal decrypt_calls
        decrypt_calls += 1
        assert actual_aad == aad
        if failure == "empty":
            return ""
        blob = bytearray(base64.urlsafe_b64decode(ciphertext[3:]))
        if failure == "tamper":
            blob[-1] ^= 1
        try:
            return aes.decrypt(
                bytes(blob[:12]),
                bytes(blob[12:]),
                b"wrong-cell" if failure == "aad" else actual_aad,
            ).decode()
        except InvalidTag:
            raise CryptoError("synthetic-private-decryption-detail") from None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        assert request.headers["x-tts-access-token"] == "synthetic-token"
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "shops": [{"id": context.binding.shop_id, "cipher": "synthetic-cipher"}],
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        resolver = TikTokCredentialResolver(
            load_app=load_app,
            load_credential=load_credential,
            decrypt=decrypt,
            http_client=http,
            clock=lambda: NOW.replace(tzinfo=None) if failure == "clock" else NOW,
        )
        if failure is None:
            reader = await resolver(context)
            reader.require_context(context)
            assert http_calls == 0
            await reader.verify_shop()
            assert http_calls == 1 and decrypt_calls == 1
        else:
            with pytest.raises(TikTokAuthorizationError) as caught:
                await resolver(context)
            assert "synthetic" not in str(caught.value)
            assert caught.value.__context__ is None or caught.value.__suppress_context__
            assert http_calls == 0
            assert decrypt_calls == (1 if failure in ("aad", "tamper", "empty") else 0)


def test_secret_contracts_are_frozen_masked_and_narrow() -> None:
    _, app, material, _, _ = fixtures()
    for contract in (app, material):
        rendered = repr(contract) + contract.model_dump_json()
        assert "synthetic-secret" not in rendered
        assert "synthetic-cipher" not in rendered
        assert material.access_token_ciphertext.get_secret_value() not in rendered
        with pytest.raises(ValidationError):
            type(contract).model_validate({**contract.model_dump(), "meta": {"arbitrary": "bad"}})
    with pytest.raises(ValidationError):
        app.enabled = False
    with pytest.raises(ValidationError):
        TikTokCredentialMaterial.model_validate(
            {
                **material.model_dump(),
                "expires_at": NOW.replace(tzinfo=None),
            }
        )
    with pytest.raises(ValidationError):
        TikTokAppMaterial.model_validate({**app.model_dump(), "app_secret": ""})


@pytest.mark.asyncio
async def test_resolver_loader_errors_propagate_without_fallback() -> None:
    context, _, _, _, _ = fixtures()

    async def load_app(_: str):
        raise RuntimeError("synthetic storage unavailable")

    async def load_credential(_: TikTokAuthorizationContext):
        pytest.fail("no fallback credential lookup")

    async with httpx.AsyncClient() as http:
        resolver = TikTokCredentialResolver(
            load_app=load_app,
            load_credential=load_credential,
            decrypt=lambda *_: pytest.fail("must not decrypt"),
            http_client=http,
        )
        with pytest.raises(RuntimeError, match="storage unavailable"):
            await resolver(context)


@requires_docker
@pytest.mark.asyncio
async def test_injected_resolver_runs_inside_fenced_native_reader(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
) -> None:
    grant = await seed(migrated_dsn)
    await create_verified_grant(pool, grant)
    context = grant.context
    operation = await begin_read(
        pool,
        context.binding.client_id,
        grant.id,
        TikTokReadRequest(kind="detail", order_id="SYNTHETIC-1"),
    )
    _, app, sample, _, _ = fixtures()
    app = app.model_copy(update={"app_id": context.app_id, "revision": context.app_revision})
    material = sample.model_copy(
        update={
            "context": context,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }
    )
    calls = []

    async def load_app(app_id: str):
        assert app_id == context.app_id
        return app

    async def load_credential(requested: TikTokAuthorizationContext):
        assert requested == context
        return material

    def decrypt(_: str, aad: bytes) -> str:
        assert aad == credential_aad(
            "channel_credentials",
            "access_token",
            str(context.binding.record_id),
        )
        return "synthetic-token"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        data = (
            {"shops": [{"id": context.binding.shop_id, "cipher": "synthetic-cipher"}]}
            if len(calls) == 1
            else {"orders": [order_snapshot()]}
        )
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        resolver = TikTokCredentialResolver(
            load_app=load_app,
            load_credential=load_credential,
            decrypt=decrypt,
            http_client=http,
        )
        result = await read_verified_order(
            pool,
            context.binding.client_id,
            operation.id,
            resolver,
        )
        assert "PRIVATE" not in result.model_dump_json()
        assert calls == ["/authorization/202309/shops", "/order/202309/orders"]
        with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
            await read_verified_order(pool, context.binding.client_id, operation.id, resolver)
        assert len(calls) == 2
