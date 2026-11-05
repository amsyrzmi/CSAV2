"""The TikTok Shop webhook (spec 6.4): verify, resolve, queue; never store the raw payload.

Synthetic shops, ids and secrets only; disposable Postgres; TikTok is ``httpx.MockTransport``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from csa.channels.marketplace import router as router_module
from csa.channels.marketplace.router import build_marketplace_router
from csa.core import db
from csa.marketplace import processor as processor_module
from csa.marketplace.disconnect import StoreNotFoundError, disconnect_store
from csa.marketplace.jobs import enqueue_job
from csa.marketplace.webhook import (
    EVENT_KINDS,
    MAX_WEBHOOK_BYTES,
    TikTokWebhookHandler,
    WebhookReceipt,
    WebhookRejected,
    event_kind,
    verify_tiktok_signature,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_marketplace_processor import (
    Clock,
    FakeTikTok,
    enqueue,
    fetch,
    job_rows,
    make_processor,
    only_active,
    order,
    order_ids,
    run_until_idle,
    store_for,
)

pytest_plugins = ["tests.test_ecommerce_grants"]

SECRET = "SYNTHETIC-WEBHOOK-APP-SECRET"
APP_KEY = "SYNTHETIC-WEBHOOK-APP-KEY"
WEBHOOK = "/api/v1/channels/tiktok_shop/webhook"
ORDER, PRODUCT, DEAUTHORIZED = 1, 5, 6  # the synthetic values match EVENT_KINDS


def sign(body: bytes, secret: str = SECRET) -> str:
    """Recipe A: HMAC-SHA256 of the raw body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def sign_b(body: bytes, secret: str = SECRET, app_key: str = APP_KEY) -> str:
    """Recipe B: HMAC-SHA256 of the app key followed by the raw body."""
    message = app_key.encode("utf-8") + body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def event(shop_id: Any, type_: Any, data: Any = None, **extra: Any) -> bytes:
    payload = {
        "type": type_,
        "tts_notification_id": "SYNTH-NOTIFICATION-1",
        "shop_id": shop_id,
        "timestamp": 1_762_000_000,
        "data": {} if data is None else data,
        **extra,
    }
    return json.dumps(payload).encode("utf-8")


async def deliver(handler: TikTokWebhookHandler, body: bytes) -> WebhookReceipt:
    return await handler.receive(raw_body=body, signature=sign(body))


class NoPool:
    """A pool that fails the test if the handler touches the database."""

    def connection(self) -> Any:
        raise AssertionError("the database must not be touched")


async def never(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("nothing may be queued or disconnected")


def offline_handler(
    secret: str | None = SECRET, app_key: str | None = APP_KEY
) -> TikTokWebhookHandler:
    return TikTokWebhookHandler(
        NoPool(),  # type: ignore[arg-type]
        secret,
        app_key,
        enqueue_job=never,
        disconnect_store=never,
    )


# ==============================================================================
# Signature, size, parsing (no database)
# ==============================================================================


def test_verify_tiktok_signature() -> None:
    body = b'{"shop_id":"SYNTH-SHOP","type":1,"timestamp":1700000000}'
    valid = sign(body)

    assert verify_tiktok_signature(body, valid, SECRET, APP_KEY) is True
    assert verify_tiktok_signature(body, valid.upper(), SECRET, APP_KEY) is True
    assert verify_tiktok_signature(body, f"sha256={valid}", SECRET, APP_KEY) is True
    assert verify_tiktok_signature(body, f"Bearer {valid}", SECRET, APP_KEY) is True
    assert verify_tiktok_signature(body, "wrong_sig", SECRET, APP_KEY) is False
    assert verify_tiktok_signature(b'{"different":"body"}', valid, SECRET, APP_KEY) is False
    assert (
        verify_tiktok_signature(body, sign(body, "SYNTHETIC-OTHER-SECRET"), SECRET, APP_KEY)
        is False
    )
    # A header Starlette decoded as latin-1 must not crash compare_digest.
    assert verify_tiktok_signature(body, "é" * 64, SECRET, APP_KEY) is False


def test_verify_accepts_either_recipe_until_partner_center_is_read() -> None:
    body = b'{"shop_id":"SYNTH-SHOP","type":1}'
    tampered = b'{"shop_id":"SYNTH-SHOP","type":2}'
    assert verify_tiktok_signature(body, sign(body), SECRET, APP_KEY) is True
    assert verify_tiktok_signature(body, sign_b(body), SECRET, APP_KEY) is True
    assert verify_tiktok_signature(body, f"sha256={sign_b(body)}", SECRET, APP_KEY) is True
    other = "SYNTHETIC-OTHER-SECRET"
    assert verify_tiktok_signature(body, sign_b(body, other), SECRET, APP_KEY) is False
    assert (
        verify_tiktok_signature(body, sign_b(body, app_key="SYNTH-KEY"), SECRET, APP_KEY) is False
    )
    assert verify_tiktok_signature(tampered, sign(body), SECRET, APP_KEY) is False
    assert verify_tiktok_signature(tampered, sign_b(body), SECRET, APP_KEY) is False


def _unknown_event_body() -> bytes:
    # Acknowledged and dropped without the database once the signature holds.
    return event("SYNTH-SHOP", 999)


@pytest.mark.parametrize("signer", [sign, sign_b], ids=["recipe-a", "recipe-b"])
async def test_body_signed_with_either_recipe_is_accepted(signer: Any) -> None:
    body = _unknown_event_body()
    receipt = await offline_handler().receive(raw_body=body, signature=signer(body))
    assert receipt == WebhookReceipt(accepted=True, queued=False)


@pytest.mark.parametrize(
    "signature_for",
    [
        lambda body: sign(body, "SYNTHETIC-OTHER-SECRET"),
        lambda body: sign_b(body, "SYNTHETIC-OTHER-SECRET"),
        lambda body: sign_b(body, app_key="SYNTHETIC-OTHER-APP-KEY"),
        lambda body: sign(body + b" "),
        lambda body: sign_b(body + b" "),
    ],
    ids=[
        "a-wrong-secret",
        "b-wrong-secret",
        "b-other-app-key",
        "a-tampered-body",
        "b-tampered-body",
    ],
)
async def test_signature_matching_neither_recipe_is_rejected(signature_for: Any) -> None:
    body = _unknown_event_body()
    with pytest.raises(WebhookRejected) as rejected:
        await offline_handler().receive(raw_body=body, signature=signature_for(body))
    assert (rejected.value.status_code, rejected.value.code) == (401, "invalid_signature")


@pytest.mark.parametrize(
    ("signature", "code"),
    [
        (None, "signature_required"),
        ("", "signature_required"),
        ("bad_signature", "invalid_signature"),
        ("éè", "invalid_signature"),
        (sign(b"not json at all", "SYNTHETIC-OTHER-SECRET"), "invalid_signature"),
    ],
)
async def test_bad_or_missing_signature_rejected_before_parsing(
    signature: str | None, code: str
) -> None:
    body = b"not json at all"
    with pytest.raises(WebhookRejected) as rejected:
        await offline_handler().receive(raw_body=body, signature=signature)
    # 401, not 422: the body is never parsed before the signature holds.
    assert (rejected.value.status_code, rejected.value.code) == (401, code)

    with pytest.raises(WebhookRejected) as parsed:
        await offline_handler().receive(raw_body=body, signature=sign(body))
    assert (parsed.value.status_code, parsed.value.code) == (422, "invalid_payload")


async def test_oversized_body_is_refused_before_the_signature_is_checked() -> None:
    body = b" " * (MAX_WEBHOOK_BYTES + 1)
    with pytest.raises(WebhookRejected) as rejected:
        await offline_handler().receive(raw_body=body, signature=sign(body))
    assert (rejected.value.status_code, rejected.value.code) == (413, "body_too_large")


@pytest.mark.parametrize(
    ("secret", "app_key"), [(None, APP_KEY), ("", APP_KEY), (SECRET, None), (SECRET, "")]
)
async def test_handler_without_a_secret_or_app_key_answers_not_found(
    secret: str | None, app_key: str | None
) -> None:
    body = event("SYNTH-SHOP", ORDER, {"order_id": "SYNTH-O"})
    with pytest.raises(WebhookRejected) as rejected:
        await offline_handler(secret, app_key).receive(raw_body=body, signature=sign(body))
    assert rejected.value.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        b"[1, 2, 3]",
        b'"a string"',
        b"null",
        b"\xff\xfe not utf-8",
        b"[" * 100_000 + b"]" * 100_000,  # deeper than the JSON parser recurses
        b'{"type": 1, ',
    ],
)
async def test_payload_that_is_not_a_json_object_is_refused_and_nothing_queued(
    body: bytes,
) -> None:
    with pytest.raises(WebhookRejected) as rejected:
        await offline_handler().receive(raw_body=body, signature=sign(body))
    assert (rejected.value.status_code, rejected.value.code) == (422, "invalid_payload")


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        (ORDER, "order"),
        (str(ORDER), "order"),
        (f" {ORDER} ", "order"),
        ("ORDER_STATUS_CHANGE", "order"),
        (PRODUCT, "product"),
        (str(PRODUCT), "product"),
        ("PRODUCT_STATUS_CHANGE", "product"),
        (DEAUTHORIZED, "deauthorized"),
        (str(DEAUTHORIZED), "deauthorized"),
        ("SELLER_DEAUTHORIZATION", "deauthorized"),
        (True, None),  # a bool is not the type number 1
        (1.0, None),
        (999, None),
        ("999", None),
        ("9" * 50, None),
        ("SOMETHING_ELSE", None),
        (None, None),
        ([1], None),
    ],
)
def test_event_kind_accepts_the_type_as_int_or_str(value: Any, kind: str | None) -> None:
    assert event_kind(value) == kind


def test_event_kinds_table_holds_only_the_three_kinds() -> None:
    assert set(EVENT_KINDS.values()) == {"order", "product", "deauthorized"}


@pytest.mark.parametrize(
    "body",
    [
        event("SYNTH-SHOP", 999, {"order_id": "SYNTH-O"}),
        event("SYNTH-SHOP", "SOMETHING_ELSE", {"order_id": "SYNTH-O"}),
        event("SYNTH-SHOP", None),
        b"{}",
        # Shop ids that are not plain identifiers never reach the database.
        event(None, ORDER, {"order_id": "SYNTH-O"}),
        event("", ORDER, {"order_id": "SYNTH-O"}),
        event("SYNTH SHOP", ORDER, {"order_id": "SYNTH-O"}),
        event("../SYNTH", ORDER, {"order_id": "SYNTH-O"}),
        event("S" * 101, ORDER, {"order_id": "SYNTH-O"}),
        event(["SYNTH-SHOP"], ORDER, {"order_id": "SYNTH-O"}),
        event(True, ORDER, {"order_id": "SYNTH-O"}),
        # Targets that are missing or not plain identifiers.
        event("SYNTH-SHOP", ORDER),
        event("SYNTH-SHOP", ORDER, "not an object"),
        event("SYNTH-SHOP", ORDER, {"order_id": ""}),
        event("SYNTH-SHOP", ORDER, {"order_id": "SYNTH/../O"}),
        event("SYNTH-SHOP", ORDER, {"order_id": "O" * 101}),
        event("SYNTH-SHOP", ORDER, {"order_id": {"nested": 1}}),
        event("SYNTH-SHOP", ORDER, {"order_id": False}),
        event("SYNTH-SHOP", ORDER, {"product_id": "SYNTH-P"}),
        event("SYNTH-SHOP", PRODUCT, {"product_id": "SYNTH P"}),
        event("SYNTH-SHOP", PRODUCT, {"order_id": "SYNTH-O"}),
    ],
)
async def test_unknown_or_unsafe_events_are_acknowledged_without_touching_the_database(
    body: bytes,
) -> None:
    receipt = await deliver(offline_handler(), body)
    assert receipt == WebhookReceipt(accepted=True, queued=False)


# ==============================================================================
# Route (no database)
# ==============================================================================


def settings_with(*, enabled: bool, secret: str | None, app_key: str | None = APP_KEY) -> MagicMock:
    settings = MagicMock()
    settings.tiktok_enabled = enabled
    settings.TIKTOK_APP_SECRET = SecretStr(secret) if secret is not None else None
    settings.TIKTOK_APP_KEY = app_key
    return settings


@pytest.mark.parametrize(("enabled", "secret"), [(False, None), (False, SECRET), (True, None)])
def test_webhook_404_when_tiktok_off(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, secret: str | None
) -> None:
    monkeypatch.setattr(
        router_module, "get_settings", lambda: settings_with(enabled=enabled, secret=secret)
    )
    built: list[Any] = []
    monkeypatch.setattr(router_module, "TikTokWebhookHandler", lambda *a, **k: built.append(a))
    app = FastAPI()
    app.include_router(build_marketplace_router())
    body = event("SYNTH-SHOP", ORDER, {"order_id": "SYNTH-O"})
    with TestClient(app) as client:
        resp = client.post(
            WEBHOOK,
            content=body,
            headers={"content-type": "application/json", "authorization": sign(body)},
        )
    assert resp.status_code == 404
    assert built == []


class RecordingHandler:
    """Stands in for TikTokWebhookHandler in the route tests."""

    instances: list[RecordingHandler] = []
    outcome: WebhookReceipt | WebhookRejected = WebhookReceipt(accepted=True, queued=True)

    def __init__(
        self, pool: Any, app_secret: str | None, app_key: str | None, **wiring: Any
    ) -> None:
        self.pool, self.app_secret, self.app_key, self.wiring = pool, app_secret, app_key, wiring
        self.calls: list[tuple[bytes, str | None]] = []
        RecordingHandler.instances.append(self)

    async def receive(self, *, raw_body: bytes, signature: str | None) -> WebhookReceipt:
        self.calls.append((raw_body, signature))
        if isinstance(self.outcome, WebhookRejected):
            raise self.outcome
        return self.outcome


@pytest.fixture
def recording_app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    RecordingHandler.instances = []
    RecordingHandler.outcome = WebhookReceipt(accepted=True, queued=True)
    monkeypatch.setattr(
        router_module, "get_settings", lambda: settings_with(enabled=True, secret=SECRET)
    )
    monkeypatch.setattr(router_module, "TikTokWebhookHandler", RecordingHandler)
    app = FastAPI()
    app.include_router(build_marketplace_router())
    app.state.pool = object()
    return app


def test_route_builds_the_handler_per_request_with_the_real_wiring(recording_app: FastAPI) -> None:
    body = event("SYNTH-SHOP", ORDER, {"order_id": "SYNTH-O"})
    with TestClient(recording_app) as client:
        resp = client.post(
            WEBHOOK,
            content=body,
            headers={"content-type": "application/json", "authorization": sign(body)},
        )
        assert resp.status_code == 200
        assert resp.json() == {"code": 0, "message": "success"}
        client.post(WEBHOOK, content=body, headers={"content-type": "application/json"})

    first, second = RecordingHandler.instances
    assert first.pool is recording_app.state.pool and first.app_secret == SECRET
    assert first.app_key == APP_KEY
    assert first.wiring == {"enqueue_job": enqueue_job, "disconnect_store": disconnect_store}
    assert first.calls == [(body, sign(body))]
    assert second.calls == [(body, None)]


@pytest.mark.parametrize(
    "headers", [{"x-tts-signature": "SYNTH-SIG"}, {}], ids=["x-tts-signature", "sign-query"]
)
def test_route_reads_the_signature_from_its_fallbacks(
    recording_app: FastAPI, headers: dict[str, str]
) -> None:
    params = {} if headers else {"sign": "SYNTH-SIG"}
    with TestClient(recording_app) as client:
        resp = client.post(
            WEBHOOK,
            content=b"{}",
            params=params,
            headers={"content-type": "application/json", **headers},
        )
    assert resp.status_code == 200
    assert RecordingHandler.instances[0].calls == [(b"{}", "SYNTH-SIG")]


def test_route_maps_a_rejection_to_its_safe_code(recording_app: FastAPI) -> None:
    RecordingHandler.outcome = WebhookRejected(401, "invalid_signature")
    with TestClient(recording_app) as client:
        resp = client.post(
            WEBHOOK,
            content=b"{}",
            headers={"content-type": "application/json", "authorization": "bad"},
        )
    assert resp.status_code == 401
    assert resp.json() == {"code": 401, "message": "invalid_signature"}


def test_route_refuses_an_oversized_stream_before_building_the_handler(
    recording_app: FastAPI,
) -> None:
    with TestClient(recording_app) as client:
        resp = client.post(
            WEBHOOK,
            content=b" " * (MAX_WEBHOOK_BYTES + 1),
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 413
    assert RecordingHandler.instances == []


def test_gateway_tiktok_webhook_content_type_enforcement(recording_app: FastAPI) -> None:
    with TestClient(recording_app) as client:
        resp = client.post(
            WEBHOOK, content="text/plain data", headers={"content-type": "text/plain"}
        )
    assert resp.status_code == 415
    assert RecordingHandler.instances == []


def test_gateway_marketplace_draft_endpoints() -> None:
    app = FastAPI()
    app.include_router(build_marketplace_router())

    with TestClient(app) as client:
        resp = client.post("/api/v1/channels/shopee/webhook")
        assert resp.status_code == 501
        assert resp.json()["platform"] == "shopee"
        assert client.get("/api/v1/channels/shopee/oauth/callback").status_code == 501

        resp = client.post("/api/v1/channels/lazada/webhook")
        assert resp.status_code == 501
        assert resp.json()["platform"] == "lazada"
        assert client.get("/api/v1/channels/lazada/oauth/callback").status_code == 501


# ==============================================================================
# Database: resolve, queue, disconnect
# ==============================================================================


@pytest_asyncio.fixture
async def owner_pool(migrated_dsn: str) -> AsyncIterator[AsyncConnectionPool]:
    pool = db.build_pool(migrated_dsn, max_size=2)
    await pool.open(wait=True)
    try:
        yield pool
    finally:
        await pool.close()


async def credential(migrated_dsn: str, store_id: Any) -> dict:
    (row,) = await fetch(
        migrated_dsn,
        "SELECT disconnected_at, access_token, refresh_token, credential_generation "
        "FROM channel_credentials WHERE id=%s",
        store_id,
    )
    return row


async def ingress_event_count(migrated_dsn: str) -> int:
    (row,) = await fetch(migrated_dsn, "SELECT count(*) AS n FROM ingress_events")
    return int(row["n"])


@requires_docker
async def test_order_event_queues_one_refresh_even_if_delivered_twice(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    binding = await store_for(migrated_dsn, pool)
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)
    body = event(binding.shop_id, ORDER, {"order_id": "SYNTH-WH-O1", "order_status": "UNPAID"})

    assert await deliver(handler, body) == WebhookReceipt(accepted=True, queued=True)
    assert await deliver(handler, body) == WebhookReceipt(accepted=True, queued=False)
    # The same change delivered with the type as a string folds into the same job.
    again = event(binding.shop_id, str(ORDER), {"order_id": "SYNTH-WH-O1"})
    assert await deliver(handler, again) == WebhookReceipt(accepted=True, queued=False)

    rows = await job_rows(migrated_dsn, binding.record_id)
    assert [(r["kind"], r["target"], r["status"]) for r in rows] == [
        ("order_refresh", "SYNTH-WH-O1", "pending")
    ]


@requires_docker
async def test_product_event_queues_product_refresh(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    binding = await store_for(migrated_dsn, pool)
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)

    body = event(binding.shop_id, PRODUCT, {"product_id": "SYNTH-WH-P1", "status": "ACTIVATE"})
    assert await deliver(handler, body) == WebhookReceipt(accepted=True, queued=True)

    rows = await job_rows(migrated_dsn, binding.record_id)
    assert [(r["kind"], r["target"], r["status"]) for r in rows] == [
        ("product_refresh", "SYNTH-WH-P1", "pending")
    ]


@requires_docker
async def test_deauthorization_disconnects_store_and_cancels_jobs(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    binding = await store_for(migrated_dsn, pool)
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)
    assert await enqueue(pool, binding, "backfill_orders") is True
    before = await credential(migrated_dsn, binding.record_id)

    deauthorized = event(binding.shop_id, DEAUTHORIZED, {"reason": "SYNTH"})
    assert await deliver(handler, deauthorized) == WebhookReceipt(accepted=True, queued=False)

    after = await credential(migrated_dsn, binding.record_id)
    assert after["disconnected_at"] is not None
    assert after["access_token"] is None and after["refresh_token"] is None
    assert after["credential_generation"] == before["credential_generation"] + 1
    rows = await job_rows(migrated_dsn, binding.record_id)
    assert [(r["kind"], r["status"], r["last_error_code"]) for r in rows] == [
        ("backfill_orders", "cancelled", "store_disconnected")
    ]

    # A repeat, and any later event for the shop, is acknowledged and changes nothing.
    assert await deliver(handler, deauthorized) == WebhookReceipt(accepted=True, queued=False)
    late = event(binding.shop_id, ORDER, {"order_id": "SYNTH-WH-LATE"})
    assert await deliver(handler, late) == WebhookReceipt(accepted=True, queued=False)
    assert await credential(migrated_dsn, binding.record_id) == after
    assert len(await job_rows(migrated_dsn, binding.record_id)) == 1


@requires_docker
async def test_deauthorization_racing_a_disconnect_is_acknowledged(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    binding = await store_for(migrated_dsn, pool)

    async def gone(*args: Any, **kwargs: Any) -> None:
        raise StoreNotFoundError

    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY, disconnect_store=gone)
    body = event(binding.shop_id, DEAUTHORIZED)
    assert await deliver(handler, body) == WebhookReceipt(accepted=True, queued=False)


@requires_docker
async def test_deauthorization_during_running_job_stops_it(
    pool: AsyncConnectionPool,
    owner_pool: AsyncConnectionPool,
    migrated_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = await store_for(migrated_dsn, pool)
    await only_active(migrated_dsn, binding.client_id)
    tiktok = FakeTikTok(binding.shop_id).install(monkeypatch)
    tiktok.orders = {
        None: ([order("SYNTH-WH-D1")], "SYNTH-WH-PAGE-2"),
        "SYNTH-WH-PAGE-2": ([order("SYNTH-WH-D2")], None),
    }
    await enqueue(pool, binding, "backfill_orders")
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)
    real_read = processor_module.read_verified
    reads = 0

    async def read_then_deauthorize(*args: Any, **kwargs: Any):
        nonlocal reads
        result = await real_read(*args, **kwargs)
        reads += 1
        if reads == 2:  # page 2 was read; TikTok deauthorizes before it is stored
            body = event(binding.shop_id, DEAUTHORIZED)
            assert await deliver(handler, body) == WebhookReceipt(accepted=True, queued=False)
        return result

    monkeypatch.setattr(processor_module, "read_verified", read_then_deauthorize)

    await run_until_idle(make_processor(owner_pool, pool, Clock()))

    assert await order_ids(migrated_dsn, binding.client_id) == ["SYNTH-WH-D1"]
    rows = await job_rows(migrated_dsn, binding.record_id)
    assert [(r["kind"], r["status"], r["last_error_code"]) for r in rows] == [
        ("backfill_orders", "cancelled", "store_disconnected")
    ]


@requires_docker
async def test_unknown_shop_and_unknown_event_are_acknowledged(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    binding = await store_for(migrated_dsn, pool)
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)

    unknown_shop = event(f"SYNTH-NO-SUCH-SHOP-{uuid4()}", ORDER, {"order_id": "SYNTH-WH-U1"})
    unknown_event = event(binding.shop_id, 999, {"order_id": "SYNTH-WH-U2"})
    for body in (unknown_shop, unknown_event):
        assert await deliver(handler, body) == WebhookReceipt(accepted=True, queued=False)

    assert await job_rows(migrated_dsn, binding.record_id) == []
    assert (
        await fetch(
            migrated_dsn,
            "SELECT id FROM marketplace_sync_jobs WHERE target IN ('SYNTH-WH-U1','SYNTH-WH-U2')",
        )
        == []
    )


@requires_docker
async def test_events_reach_only_the_active_store_of_their_own_tenant(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    mine = await store_for(migrated_dsn, pool)
    other = await store_for(migrated_dsn, pool)
    gone = await store_for(migrated_dsn, pool)
    await disconnect_store(pool, gone.client_id, gone.record_id, reason="admin")
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)

    assert await deliver(
        handler, event(mine.shop_id, ORDER, {"order_id": "SYNTH-WH-T1"})
    ) == WebhookReceipt(accepted=True, queued=True)
    assert await deliver(
        handler, event(gone.shop_id, ORDER, {"order_id": "SYNTH-WH-T2"})
    ) == WebhookReceipt(accepted=True, queued=False)

    jobs = await fetch(
        migrated_dsn,
        "SELECT client_id, store_id, target FROM marketplace_sync_jobs "
        "WHERE target IN ('SYNTH-WH-T1','SYNTH-WH-T2')",
    )
    assert jobs == [
        {"client_id": mine.client_id, "store_id": mine.record_id, "target": "SYNTH-WH-T1"}
    ]
    assert await job_rows(migrated_dsn, other.record_id) == []
    assert await job_rows(migrated_dsn, gone.record_id) == []


@requires_docker
async def test_no_ingress_events_row_written(pool: AsyncConnectionPool, migrated_dsn: str) -> None:
    binding = await store_for(migrated_dsn, pool)
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)
    before = await ingress_event_count(migrated_dsn)

    bodies = [
        event(binding.shop_id, ORDER, {"order_id": "SYNTH-WH-I1"}),
        event(binding.shop_id, ORDER, {"order_id": "SYNTH-WH-I1"}),
        event(binding.shop_id, PRODUCT, {"product_id": "SYNTH-WH-I2"}),
        event(binding.shop_id, 999),
        event(f"SYNTH-NO-SUCH-SHOP-{uuid4()}", ORDER, {"order_id": "SYNTH-WH-I3"}),
        event(binding.shop_id, DEAUTHORIZED),
    ]
    for body in bodies:
        assert (await deliver(handler, body)).accepted is True
    with pytest.raises(WebhookRejected):
        await handler.receive(raw_body=bodies[0], signature="bad")

    assert await ingress_event_count(migrated_dsn) == before


@requires_docker
async def test_logs_carry_no_payload_shop_order_or_signature(
    pool: AsyncConnectionPool, migrated_dsn: str
) -> None:
    binding = await store_for(migrated_dsn, pool)
    handler = TikTokWebhookHandler(pool, SECRET, APP_KEY)
    order_body = event(binding.shop_id, ORDER, {"order_id": "SYNTH-WH-L1"})
    unknown = event(f"SYNTH-NO-SUCH-SHOP-{uuid4()}", ORDER, {"order_id": "SYNTH-WH-L2"})

    with capture_logs() as logs:
        await deliver(handler, order_body)
        await deliver(handler, order_body)
        await deliver(handler, unknown)
        await deliver(handler, event(binding.shop_id, PRODUCT, {"product_id": "SYNTH-WH-L3"}))
        await deliver(handler, event(binding.shop_id, 999))
        with pytest.raises(WebhookRejected):
            await handler.receive(raw_body=order_body, signature="SYNTH-BAD-SIGNATURE")
        await deliver(handler, event(binding.shop_id, DEAUTHORIZED))

    text = repr(logs)
    assert logs, "the handler logs its outcomes"
    for forbidden in (
        binding.shop_id,
        "SYNTH-WH-L1",
        "SYNTH-WH-L2",
        "SYNTH-WH-L3",
        "SYNTH-NO-SUCH-SHOP",
        "SYNTH-NOTIFICATION-1",
        "SYNTH-BAD-SIGNATURE",
        sign(order_body),
        SECRET,
    ):
        assert forbidden not in text


@requires_docker
async def test_signed_order_event_through_the_route_queues_a_refresh(
    pool: AsyncConnectionPool, migrated_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    binding = await store_for(migrated_dsn, pool)
    monkeypatch.setattr(
        router_module, "get_settings", lambda: settings_with(enabled=True, secret=SECRET)
    )
    app = FastAPI()
    app.include_router(build_marketplace_router())
    app.state.pool = pool
    body = event(binding.shop_id, ORDER, {"order_id": "SYNTH-WH-R1"})

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://hooks.test") as client:
        resp = await client.post(
            WEBHOOK,
            content=body,
            headers={"content-type": "application/json", "authorization": sign(body)},
        )
        refused = await client.post(
            WEBHOOK,
            content=body,
            headers={"content-type": "application/json", "authorization": "bad"},
        )

    assert resp.status_code == 200 and resp.json() == {"code": 0, "message": "success"}
    assert refused.status_code == 401
    rows = await job_rows(migrated_dsn, binding.record_id)
    assert [(r["kind"], r["target"]) for r in rows] == [("order_refresh", "SYNTH-WH-R1")]
