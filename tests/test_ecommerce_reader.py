"""Bound server read integration with synthetic credentials and HTTP only."""

from uuid import uuid4

import httpx
import pytest
from csa.agents.ecommerce.adapters.tiktok_shop import TikTokReadError
from csa.agents.ecommerce.authorization import TikTokAuthorizationContext, TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest, begin_read
from csa.agents.ecommerce.grants import create_verified_grant, invalidate_grant
from csa.agents.ecommerce.reader import BoundTikTokReader, read_verified_order
from psycopg_pool import AsyncConnectionPool

from tests.conftest import requires_docker
from tests.test_ecommerce_agent import _sample_creds
from tests.test_ecommerce_grants import seed
from tests.test_tiktok_snapshots import order_snapshot

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_ecommerce_grants"]


@pytest.mark.parametrize("kind", ["detail", "search"])
async def test_bound_reader_verifies_shop_then_exact_persisted_order_request_once(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    kind: str,
) -> None:
    grant = await seed(migrated_dsn)
    await create_verified_grant(pool, grant)
    tenant = grant.context.binding.client_id
    request = (
        TikTokReadRequest(kind="detail", order_id="SYNTHETIC-1")
        if kind == "detail"
        else TikTokReadRequest(kind="search", limit=1, page_token="opaque/+&=%")
    )
    operation = await begin_read(pool, tenant, grant.id, request)
    creds = _sample_creds().model_copy(update={"shop_id": grant.context.binding.shop_id})
    calls = []

    def handler(http: httpx.Request) -> httpx.Response:
        calls.append(http.url.path)
        assert http.headers["x-tts-access-token"] == creds.access_token
        assert "access_token" not in http.url.params
        if len(calls) == 1:
            assert http.url.path == "/authorization/202309/shops"
            assert "shop_cipher" not in http.url.params
            data = {"shops": [{"id": creds.shop_id, "cipher": creds.shop_cipher}]}
        else:
            assert http.url.params["shop_cipher"] == creds.shop_cipher
            if kind == "detail":
                assert http.url.params["ids"] == "SYNTHETIC-1"
            else:
                assert http.url.params["page_token"] == "opaque/+&=%"
                assert http.url.params["page_size"] == "1"
            data = {"orders": [order_snapshot()]}
        return httpx.Response(200, json={"code": 0, "data": data})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def resolve(context: TikTokAuthorizationContext, conn) -> BoundTikTokReader:
            assert context == grant.context
            return BoundTikTokReader(
                context,
                creds,
                expected_app_key=creds.app_key,
                http_client=http,
            )

        result = await read_verified_order(pool, tenant, operation.id, resolve)
        assert "PRIVATE" not in result.model_dump_json()
        assert len(calls) == 2
        with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
            await read_verified_order(pool, tenant, operation.id, resolve)
        assert len(calls) == 2


@pytest.mark.parametrize("failure", ["shop", "permission", "timeout", "binding", "revoked"])
async def test_bound_reader_fails_closed_without_retry(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    failure: str,
) -> None:
    grant = await seed(migrated_dsn)
    await create_verified_grant(pool, grant)
    tenant = grant.context.binding.client_id
    operation = await begin_read(
        pool,
        tenant,
        grant.id,
        TikTokReadRequest(kind="detail", order_id="SYNTHETIC-1"),
    )
    creds = _sample_creds().model_copy(update={"shop_id": grant.context.binding.shop_id})
    calls = []
    resolves = 0

    def handler(http: httpx.Request) -> httpx.Response:
        calls.append(http.url.path)
        if http.url.path == "/authorization/202309/shops":
            shops = (
                [] if failure == "shop" else [{"id": creds.shop_id, "cipher": creds.shop_cipher}]
            )
            return httpx.Response(200, json={"code": 0, "data": {"shops": shops}})
        if failure == "timeout":
            raise httpx.ReadTimeout("private synthetic", request=http)
        return httpx.Response(200, json={"code": 105001, "message": "private permission denied"})

    if failure == "revoked":
        await invalidate_grant(pool, tenant, grant.id, expected_revision=1, state="revoked")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:

        async def resolve(context: TikTokAuthorizationContext, conn) -> BoundTikTokReader:
            nonlocal resolves
            resolves += 1
            if failure == "binding":
                raw = context.model_dump()
                raw["binding"]["client_id"] = uuid4()
                context = TikTokAuthorizationContext.model_validate(raw)
            return BoundTikTokReader(
                context,
                creds,
                expected_app_key=creds.app_key,
                http_client=http,
            )

        with pytest.raises((TikTokReadError, TikTokAuthorizationError)) as caught:
            await read_verified_order(pool, tenant, operation.id, resolve)
        assert "private" not in str(caught.value)
        assert (
            len(calls)
            == {"shop": 1, "permission": 2, "timeout": 2, "binding": 0, "revoked": 0}[failure]
        )
        if failure in ("permission", "timeout"):
            with pytest.raises(TikTokAuthorizationError, match="operation_consumed"):
                await read_verified_order(pool, tenant, operation.id, resolve)
            assert len(calls) == 2 and resolves == 1
        if failure == "revoked":
            assert resolves == 0


@pytest.mark.parametrize("change", ["app_key", "shop_id", "shop_cipher"])
async def test_bound_adapter_rejects_local_mismatch_before_http(
    pool: AsyncConnectionPool,
    migrated_dsn: str,
    change: str,
) -> None:
    grant = await seed(migrated_dsn)
    creds = _sample_creds().model_copy(update={"shop_id": grant.context.binding.shop_id})
    wrong = creds.model_copy(update={change: "" if change == "shop_cipher" else "WRONG"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: pytest.fail("binding rejection must not perform HTTP")
        )
    ) as http:
        with pytest.raises(TikTokAuthorizationError, match="adapter_binding_mismatch"):
            BoundTikTokReader(
                grant.context,
                wrong,
                expected_app_key=creds.app_key,
                http_client=http,
            )
