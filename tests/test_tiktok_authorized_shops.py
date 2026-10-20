"""Mocked official shop lookup shape; no live tokens or grants."""

from typing import Any

import httpx
import pytest
from csa.agents.ecommerce import TikTokReadError, TikTokShopAdapter

from tests.test_ecommerce_agent import _sample_creds


@pytest.mark.asyncio
async def test_authorized_shop_request_exact_match_and_no_cached_success() -> None:
    creds = _sample_creds().model_copy(update={"shop_id": "SYNTH-SHOP"})
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.method == "GET"
        assert request.url.path == "/authorization/202309/shops"
        params = dict(request.url.params)
        assert set(params) == {"app_key", "timestamp", "sign"}
        assert params["sign"] == adapter.sign_request(request.url.path, params, "")
        assert request.headers["x-tts-access-token"] == creds.access_token
        assert request.headers["content-type"] == "application/json"
        assert request.content == b""
        shops = (
            [
                {"id": "OTHER", "cipher": "synthetic-other"},
                {"id": creds.shop_id, "cipher": creds.shop_cipher, "name": "excluded"},
            ]
            if calls == 1
            else []
        )
        return httpx.Response(200, json={"code": 0, "data": {"shops": shops}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        assert await adapter.require_authorized_shop("SYNTH-SHOP") is None
        with pytest.raises(TikTokReadError, match="shop_not_authorized"):
            await adapter.require_authorized_shop("SYNTH-SHOP")
    assert calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({}, "invalid_response"),
        ({"shops": None}, "invalid_response"),
        ({"shops": [{}]}, "invalid_response"),
        ({"shops": [{"id": "SYNTH-SHOP"}]}, "invalid_response"),
        ({"shops": [{"id": "SYNTH-SHOP", "cipher": ""}]}, "invalid_response"),
        ({"shops": [{"id": "", "cipher": "private"}]}, "invalid_response"),
        ({"shops": [{"id": "SYNTH-SHOP", "cipher": 123}]}, "invalid_response"),
        (
            {
                "shops": [
                    {"id": "SYNTH-SHOP", "cipher": "private"},
                    {"id": "SYNTH-SHOP", "cipher": "private"},
                ]
            },
            "invalid_response",
        ),
        ({"shops": []}, "shop_not_authorized"),
        ({"shops": [{"id": "OTHER", "cipher": "private"}]}, "shop_not_authorized"),
        ({"shops": [{"id": "SYNTH-SHOP", "cipher": "private-wrong"}]}, "shop_binding_mismatch"),
    ],
)
async def test_malformed_or_wrong_shop_is_never_authorized(data: dict[str, Any], code: str) -> None:
    creds = _sample_creds().model_copy(update={"shop_id": "SYNTH-SHOP"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"code": 0, "data": data}),
        )
    ) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        with pytest.raises(TikTokReadError) as caught:
            await adapter.require_authorized_shop("SYNTH-SHOP")
        assert caught.value.code == code
        assert "private" not in str(caught.value)
        assert caught.value.__context__ is None or caught.value.__suppress_context__


@pytest.mark.asyncio
@pytest.mark.parametrize("shop_id", ["", "x" * 101, "OTHER"])
async def test_wrong_local_binding_never_calls_provider(shop_id: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("wrong local binding must not call provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        adapter = TikTokShopAdapter(
            _sample_creds().model_copy(update={"shop_id": "SYNTH-SHOP"}), http_client=client
        )
        with pytest.raises(TikTokReadError, match="shop_binding_mismatch"):
            await adapter.require_authorized_shop(shop_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["rejected", "http", "redirect", "timeout", "json"])
async def test_shop_errors_are_safe_and_do_not_follow_redirects(failure: str) -> None:
    calls = 0
    creds = _sample_creds().model_copy(update={"shop_id": "SYNTH-SHOP"})

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("private-detail", request=request)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid/private"})
        if failure == "http":
            return httpx.Response(403, text=creds.access_token)
        if failure == "json":
            return httpx.Response(200, text="private-invalid")
        return httpx.Response(200, json={"code": 105001, "message": "private-rejected"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as client:
        adapter = TikTokShopAdapter(creds, http_client=client)
        with pytest.raises(TikTokReadError) as caught:
            await adapter.require_authorized_shop("SYNTH-SHOP")
        assert (
            caught.value.code
            == {
                "rejected": "provider_rejected",
                "http": "http_error",
                "redirect": "http_error",
                "timeout": "transport_error",
                "json": "invalid_response",
            }[failure]
        )
        assert "private" not in str(caught.value)
        assert creds.access_token not in str(caught.value)
        assert caught.value.__context__ is None or caught.value.__suppress_context__
    assert calls == 1
