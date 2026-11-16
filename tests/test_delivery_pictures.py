"""Delivery of image rows: sendPhoto at send time, two attempts, then skipped (never dead)."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, LiteralString

import httpx
import psycopg
import pytest
import structlog
import structlog.testing
from apps.delivery.main import MAX_PICTURE_ATTEMPTS, PICTURE_CODES, DeliveryWorker
from csa.contracts.envelope import Channel, Contact, ConversationRef, Envelope, Message, Meta
from csa.contracts.facts import PictureRef, SentPictureKind
from csa.contracts.state import OutboundMessage
from csa.core import crypto, db, settings
from csa.core.tenancy import tx
from csa.media import service
from csa.media.images import check_image
from csa.turn.deps import _insert_outbound
from psycopg.types.json import Jsonb

from tests.conftest import owner_dsn_for, requires_docker
from tests.media_images import png

_KEY = "base64:" + base64.b64encode(b"\x07" * 32).decode("ascii")
BOT_TOKEN = "123456:synthetic-picture-bot-token"
CHAT = "501"
CAPTION = "DuitNow QR: Synthetic Kedai"
TEXT = "Scan the QR to pay, then send us the receipt."
PICTURE = png(800, 800)


def _configure(monkeypatch: pytest.MonkeyPatch, *, app_dsn: str, owner_dsn: str) -> None:
    values = {
        "ENV": "ci",
        "DATABASE_URL": app_dsn,
        "LG_DATABASE_URL": owner_dsn,
        "GROQ_API_KEY": "test-key",
        "PUBLIC_BASE_URL": "https://example.test",
        "CREDENTIALS_KEY": _KEY,
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    for name in ("WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    settings.get_settings.cache_clear()


@dataclass
class Shop:
    """One synthetic company with a Telegram chat, a QR method, a plan and a product."""

    client_id: uuid.UUID
    endpoint_id: uuid.UUID
    contact_id: uuid.UUID
    conversation_id: uuid.UUID
    method_id: uuid.UUID
    plan_code: str
    product_id: uuid.UUID
    traces: list[uuid.UUID] = field(default_factory=list)

    def subject(self, kind: SentPictureKind) -> str:
        return {
            "payment_qr": str(self.method_id),
            "plan": self.plan_code,
            "product": str(self.product_id),
        }[kind]


def _seed_shop(owner_dsn: str, *, channel: str = "telegram") -> Shop:
    client_id, credential_id, endpoint_id, contact_id, conversation_id, method_id, product_id = (
        uuid.uuid4() for _ in range(7)
    )
    token = crypto.encrypt(
        BOT_TOKEN,
        crypto.credential_aad("channel_credentials", "access_token", str(credential_id)),
    )
    shop = Shop(
        client_id=client_id,
        endpoint_id=endpoint_id,
        contact_id=contact_id,
        conversation_id=conversation_id,
        method_id=method_id,
        plan_code="gold",
        product_id=product_id,
    )
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clients (id, slug, name, platform_version) VALUES (%s, %s, %s, %s)",
            (client_id, f"pic-{client_id}", "Synthetic Picture Shop", "pic-test"),
        )
        conn.execute("SELECT public.seed_core_capabilities(%s)", (client_id,))
        conn.execute(
            "INSERT INTO channel_credentials (id, client_id, channel, shop_id, access_token) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                credential_id,
                client_id,
                channel,
                f"pn-{client_id.hex[:12]}" if channel == "whatsapp" else None,
                token,
            ),
        )
        conn.execute(
            "INSERT INTO webhook_endpoints (id, client_id, public_id, credential_id) "
            "VALUES (%s, %s, %s, %s)",
            (endpoint_id, client_id, f"pic-{uuid.uuid4().hex}", credential_id),
        )
        conn.execute(
            "INSERT INTO staff_notification_targets (client_id, channel, address) "
            "VALUES (%s, 'telegram', '999')",
            (client_id,),
        )
        conn.execute(
            "INSERT INTO contacts (id, client_id, full_name) VALUES (%s, %s, 'Synthetic')",
            (contact_id, client_id),
        )
        conn.execute(
            "INSERT INTO conversations "
            "(id, client_id, contact_id, channel, channel_thread, last_inbound_at) "
            "VALUES (%s, %s, %s, %s, %s, now())",
            (conversation_id, client_id, contact_id, channel, CHAT),
        )
        conn.execute(
            "INSERT INTO payment_methods (id, client_id, kind, label) VALUES (%s, %s, 'qr', %s)",
            (method_id, client_id, "Synthetic DuitNow"),
        )
        conn.execute(
            "INSERT INTO plans (client_id, code, name, price) VALUES (%s, %s, 'Gold', 10)",
            (client_id, shop.plan_code),
        )
        conn.execute(
            "INSERT INTO products (id, client_id, sku, name, price) "
            "VALUES (%s, %s, 'SKU-1', 'Synthetic Mug', 12)",
            (product_id, client_id),
        )
    return shop


def _envelope(shop: Shop, owner_dsn: str, *, channel: Channel = Channel.TELEGRAM) -> Envelope:
    """A turn's envelope, with the ingress event the writer finds the endpoint from."""
    trace = uuid.uuid4()
    shop.traces.append(trace)
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO ingress_events (id, client_id, endpoint_id, contact_id, "
            "idempotency_key, payload_hash, trace_id, raw) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                uuid.uuid4(),
                shop.client_id,
                shop.endpoint_id,
                shop.contact_id,
                f"pic:{trace}",
                hashlib.sha256(b"x").hexdigest(),
                trace,
                Jsonb({}),
            ),
        )
    return Envelope(
        trace_id=trace,
        received_at=datetime.now(UTC),
        channel=channel,
        idempotency_key=f"pic:{trace}",
        client_id=shop.client_id,
        contact=Contact(
            id=shop.contact_id, external_id=CHAT, channel=channel, display_name="Synthetic"
        ),
        conversation=ConversationRef(
            id=shop.conversation_id, channel_thread=CHAT, turn_count=1, status="open"
        ),
        message=Message(body_raw="pay", body_redacted="pay", language="en"),
        meta=Meta(),
    )


def _image(env: Envelope, kind: SentPictureKind, subject: str) -> OutboundMessage:
    assert env.contact.id is not None
    return OutboundMessage(
        trace_id=env.trace_id,
        contact_id=env.contact.id,
        channel=env.channel,
        kind="image",
        body=CAPTION,
        picture=PictureRef(kind=kind, subject_key=subject, caption=CAPTION),
        idempotency_key=f"{env.trace_id}:0:picture",
    )


def _text(env: Envelope) -> OutboundMessage:
    assert env.contact.id is not None
    return OutboundMessage(
        trace_id=env.trace_id,
        contact_id=env.contact.id,
        channel=env.channel,
        body=TEXT,
        idempotency_key=f"{env.trace_id}:0",
    )


def _response(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": {"message_id": 11}})


def _method(request: httpx.Request) -> str:
    return request.url.path.rsplit("/", 1)[-1]


@dataclass
class Harness:
    owner_dsn: str
    app_pool: Any
    worker: DeliveryWorker
    requests: list[httpx.Request]
    handler: list[Callable[[httpx.Request], httpx.Response]]

    async def put_picture(self, shop: Shop, kind: SentPictureKind, data: bytes = PICTURE) -> Any:
        async with tx(self.app_pool, str(shop.client_id)) as conn:
            stored = await service.put(
                conn,
                shop.client_id,
                kind,
                shop.subject(kind),
                data,
                check_image(data, None),
                source="upload",
                actor_id=None,
            )
        assert stored is not None
        return stored.id

    async def write(self, shop: Shop, *messages_for: str, kind: SentPictureKind = "payment_qr"):
        """The turn's writer (Task 7): image then text, in one transaction."""
        env = _envelope(shop, self.owner_dsn)
        async with tx(self.app_pool, str(shop.client_id)) as conn:
            for what in messages_for:
                message = _image(env, kind, shop.subject(kind)) if what == "image" else _text(env)
                await _insert_outbound(conn, env, message)
        return env

    def deliveries(self, shop: Shop) -> list[dict[str, Any]]:
        with psycopg.connect(self.owner_dsn) as conn:
            cur = conn.execute(
                "SELECT d.id, d.status, d.attempts, d.last_status_code, d.last_error,"
                " extract(epoch FROM d.next_attempt_at - now()) AS wait,"
                " m.has_attachment"
                " FROM outbound_deliveries d JOIN messages m ON m.id = d.message_id"
                " WHERE d.client_id = %s ORDER BY d.created_at, d.id",
                (shop.client_id,),
            )
            names = [c.name for c in cur.description or []]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def image_row(self, shop: Shop) -> dict[str, Any]:
        return next(r for r in self.deliveries(shop) if r["has_attachment"])

    def text_row(self, shop: Shop) -> dict[str, Any]:
        return next(r for r in self.deliveries(shop) if not r["has_attachment"])

    def make_due(self, shop: Shop) -> None:
        with psycopg.connect(self.owner_dsn, autocommit=True) as conn:
            conn.execute(
                "UPDATE outbound_deliveries SET next_attempt_at = now()"
                " WHERE client_id = %s AND status = 'pending'",
                (shop.client_id,),
            )

    def attempts(self, shop: Shop) -> list[tuple[int, int | None, str | None]]:
        with psycopg.connect(self.owner_dsn) as conn:
            return [
                (int(r[0]), r[1], r[2])
                for r in conn.execute(
                    "SELECT a.attempt_no, a.status_code, a.error FROM outbound_delivery_attempts a"
                    " JOIN outbound_deliveries d ON d.id = a.delivery_id"
                    " JOIN messages m ON m.id = d.message_id"
                    " WHERE d.client_id = %s AND m.has_attachment ORDER BY a.id",
                    (shop.client_id,),
                ).fetchall()
            ]

    def dead_letters(self, shop: Shop) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
        with psycopg.connect(self.owner_dsn) as conn:
            return conn.execute(
                "SELECT stage, envelope, error FROM dlq WHERE client_id = %s ORDER BY created_at",
                (shop.client_id,),
            ).fetchall()

    def notices(self, shop: Shop) -> list[tuple[str, dict[str, Any]]]:
        with psycopg.connect(self.owner_dsn) as conn:
            return conn.execute(
                "SELECT event, payload FROM staff_notifications WHERE client_id = %s",
                (shop.client_id,),
            ).fetchall()

    def owner(self, sql: LiteralString, *params: object) -> None:
        with psycopg.connect(self.owner_dsn, autocommit=True) as conn:
            conn.execute(sql, params)

    async def send(self) -> bool:
        return await self.worker.process_once()


@asynccontextmanager
async def harness(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Harness]:
    owner_dsn = owner_dsn_for(pg_container)
    _configure(monkeypatch, app_dsn=app_dsn, owner_dsn=owner_dsn)
    # isolate from rows other tests left pending in the shared container
    with psycopg.connect(owner_dsn, autocommit=True) as conn:
        conn.execute(
            "UPDATE outbound_deliveries SET status = 'skipped', last_error = 'test_isolation'"
            " WHERE status IN ('pending', 'delivering')"
        )
    owner_pool = db.build_pool(owner_dsn, max_size=4)
    app_pool = db.build_pool(app_dsn, max_size=4)
    await owner_pool.open(wait=True)
    await app_pool.open(wait=True)
    requests: list[httpx.Request] = []
    handler: list[Callable[[httpx.Request], httpx.Response]] = [_response]

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler[0](request)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            worker = DeliveryWorker(owner_pool, app_pool, http)

            async def no_throttle(_chat_id: str) -> None:
                return None

            monkeypatch.setattr(worker, "_throttle", no_throttle)
            yield Harness(owner_dsn, app_pool, worker, requests, handler)
    finally:
        await app_pool.close()
        await owner_pool.close()
        settings.get_settings.cache_clear()


def _form_field(request: httpx.Request, name: str) -> bytes:
    """One multipart field's value from a sendPhoto body."""
    body = request.content
    marker = f'name="{name}"'.encode()
    start = body.index(marker)
    value_start = body.index(b"\r\n\r\n", start) + 4
    value_end = body.index(b"\r\n--", value_start)
    return body[value_start:value_end]


def _assert_skipped(
    h: Harness, shop: Shop, code: str, *, attempts: int, status_code: int | None = None
) -> None:
    assert code in PICTURE_CODES
    row = h.image_row(shop)
    assert row["status"] == "skipped"
    assert row["attempts"] == attempts
    assert row["last_error"] == code
    if status_code is not None:
        assert row["last_status_code"] == status_code
    [(stage, envelope, error)] = h.dead_letters(shop)
    assert stage == "delivery"
    assert envelope["delivery_id"] == str(row["id"])
    assert envelope["body"] == CAPTION
    assert error == {"error_code": code, "retryable": False}
    [(event, payload)] = h.notices(shop)
    assert event == "dlq"
    assert payload["stage"] == "delivery"
    assert payload["reason"] == code
    assert payload["delivery_id"] == str(row["id"])


@requires_docker
async def test_image_then_text_are_claimed_in_that_order(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image", "text")
        assert await h.send() is True
        assert await h.send() is True
        assert [_method(r) for r in h.requests] == ["sendPhoto", "sendMessage"]
        assert [r["status"] for r in h.deliveries(shop)] == ["delivered", "delivered"]
        assert h.image_row(shop)["attempts"] == 1


@requires_docker
@pytest.mark.parametrize("kind", ["payment_qr", "plan", "product"])
async def test_image_is_sent_with_send_photo_and_its_caption(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch, kind: SentPictureKind
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, kind)
        await h.write(shop, "image", kind=kind)
        assert await h.send() is True
        [request] = h.requests
        assert request.url.path == f"/bot{BOT_TOKEN}/sendPhoto"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert _form_field(request, "chat_id") == CHAT.encode()
        assert _form_field(request, "caption") == CAPTION.encode()
        assert PICTURE in request.content
        assert b"image/png" in request.content
        row = h.image_row(shop)
        assert row["status"] == "delivered" and row["last_status_code"] == 200
        assert h.dead_letters(shop) == [] and h.notices(shop) == []


@requires_docker
async def test_replaced_picture_sends_the_new_bytes(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        first = await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image")
        replacement = png(640, 480)
        assert await h.put_picture(shop, "payment_qr", replacement) == first
        assert await h.send() is True
        [request] = h.requests
        assert replacement in request.content
        assert PICTURE not in request.content
        assert h.image_row(shop)["status"] == "delivered"


@requires_docker
async def test_removed_picture_is_skipped_picture_unavailable_and_the_text_follows(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        asset = await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image", "text")
        async with tx(h.app_pool, str(shop.client_id)) as conn:
            assert await service.delete(conn, shop.client_id, asset)
        assert await h.send() is True
        assert h.requests == []
        _assert_skipped(h, shop, "picture_unavailable", attempts=1)
        assert await h.send() is True
        assert [_method(r) for r in h.requests] == ["sendMessage"]
        assert h.text_row(shop)["status"] == "delivered"


@requires_docker
@pytest.mark.parametrize(
    ("kind", "retire"),
    [
        ("payment_qr", "UPDATE payment_methods SET is_active = false WHERE client_id = %s"),
        ("payment_qr", "UPDATE payment_methods SET kind = 'transfer' WHERE client_id = %s"),
        ("plan", "UPDATE plans SET is_active = false WHERE client_id = %s"),
        ("product", "UPDATE products SET is_active = false WHERE client_id = %s"),
    ],
)
async def test_inactive_subject_is_skipped_picture_unavailable(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    kind: SentPictureKind,
    retire: LiteralString,
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, kind)
        await h.write(shop, "image", "text", kind=kind)
        h.owner(retire, shop.client_id)
        assert await h.send() is True
        assert h.requests == []
        _assert_skipped(h, shop, "picture_unavailable", attempts=1)
        assert await h.send() is True
        assert [_method(r) for r in h.requests] == ["sendMessage"]


@requires_docker
@pytest.mark.parametrize("status", [400, 413])
async def test_telegram_400_is_skipped_picture_rejected_at_once(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image", "text")
        h.handler[0] = lambda request: (
            httpx.Response(status, json={"ok": False, "description": "Bad Request"})
            if _method(request) == "sendPhoto"
            else _response(request)
        )
        assert await h.send() is True
        _assert_skipped(h, shop, "picture_rejected", attempts=1, status_code=status)
        assert h.attempts(shop) == [(1, status, "picture_rejected")]
        assert await h.send() is True
        assert [_method(r) for r in h.requests] == ["sendPhoto", "sendMessage"]


@requires_docker
async def test_two_failures_end_skipped_picture_failed_and_the_text_follows(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image", "text")
        h.handler[0] = lambda request: (
            httpx.Response(500, json={"ok": False})
            if _method(request) == "sendPhoto"
            else _response(request)
        )
        assert await h.send() is True
        first = h.image_row(shop)
        assert first["status"] == "pending" and first["attempts"] == 1
        assert first["last_error"] == "telegram_http_500"
        # The text waits behind a pending image.
        assert h.text_row(shop)["status"] == "pending"
        assert h.dead_letters(shop) == []
        h.make_due(shop)
        assert await h.send() is True
        _assert_skipped(h, shop, "picture_failed", attempts=MAX_PICTURE_ATTEMPTS, status_code=500)
        assert h.attempts(shop) == [(1, 500, "telegram_http_500"), (2, 500, "picture_failed")]
        assert all(r["status"] != "dead" for r in h.deliveries(shop))
        # The text is claimed right after, with no wait.
        assert await h.send() is True
        assert [_method(r) for r in h.requests] == ["sendPhoto", "sendPhoto", "sendMessage"]
        assert h.text_row(shop)["status"] == "delivered"


@requires_docker
async def test_rate_limit_retry_after_is_honoured_within_two_attempts(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image", "text")
        h.handler[0] = lambda request: httpx.Response(
            429, json={"ok": False, "parameters": {"retry_after": 7}}
        )
        assert await h.send() is True
        row = h.image_row(shop)
        assert row["status"] == "pending" and row["attempts"] == 1
        assert row["last_error"] == "telegram_rate_limited"
        assert 5 < float(row["wait"]) <= 7
        h.make_due(shop)
        h.handler[0] = _response
        assert await h.send() is True
        row = h.image_row(shop)
        assert row["status"] == "delivered" and row["attempts"] == MAX_PICTURE_ATTEMPTS
        assert h.dead_letters(shop) == []


@requires_docker
async def test_stale_image_lease_after_two_attempts_is_skipped(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image", "text")
        image_id = h.image_row(shop)["id"]
        h.owner(
            "UPDATE outbound_deliveries SET status = 'delivering', attempts = 2,"
            " locked_at = now() - interval '1 hour' WHERE id = %s",
            image_id,
        )
        assert await h.worker.reap() == 1
        _assert_skipped(h, shop, "picture_failed", attempts=2)
        assert await h.send() is True
        assert [_method(r) for r in h.requests] == ["sendMessage"]

        # A stale image row with one attempt left is put back, as a text row is.
        shop2 = _seed_shop(h.owner_dsn)
        await h.put_picture(shop2, "payment_qr")
        await h.write(shop2, "image")
        h.owner(
            "UPDATE outbound_deliveries SET status = 'delivering', attempts = 1,"
            " locked_at = now() - interval '1 hour' WHERE client_id = %s",
            shop2.client_id,
        )
        assert await h.worker.reap() == 1
        assert h.image_row(shop2)["status"] == "pending"
        assert h.dead_letters(shop2) == []


@requires_docker
async def test_delivery_never_reads_another_companys_picture(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop_a, shop_b = _seed_shop(h.owner_dsn), _seed_shop(h.owner_dsn)
        await h.put_picture(shop_a, "payment_qr")
        other = await h.put_picture(shop_b, "payment_qr", png(320, 320))
        await h.write(shop_a, "image", "text")
        h.owner(
            "UPDATE messages SET attachments = %s WHERE client_id = %s AND has_attachment",
            Jsonb([{"type": "image", "asset_id": str(other)}]),
            shop_a.client_id,
        )
        assert await h.send() is True
        assert h.requests == []
        _assert_skipped(h, shop_a, "picture_unavailable", attempts=1)
        assert await h.send() is True
        assert [_method(r) for r in h.requests] == ["sendMessage"]


@requires_docker
async def test_image_row_on_whatsapp_is_skipped_picture_unavailable(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        monkeypatch.setenv("WHATSAPP_APP_SECRET", "synthetic-secret")
        monkeypatch.setenv("WHATSAPP_VERIFY_TOKEN", "synthetic-verify")
        settings.get_settings.cache_clear()
        shop = _seed_shop(h.owner_dsn, channel="whatsapp")
        asset = await h.put_picture(shop, "payment_qr")
        # The writer never makes one off Telegram; an older or hand-made row still must not send.
        await h.write(shop, "text")
        h.owner(
            "UPDATE messages SET attachments = %s, has_attachment = true WHERE client_id = %s",
            Jsonb([{"type": "image", "asset_id": str(asset)}]),
            shop.client_id,
        )
        assert await h.send() is True
        assert h.requests == []
        row = h.deliveries(shop)[0]
        assert row["status"] == "skipped" and row["last_error"] == "picture_unavailable"


@requires_docker
async def test_text_rows_keep_five_attempts(
    pg_container: object, app_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.write(shop, "text")
        h.handler[0] = lambda request: httpx.Response(500, json={"ok": False})
        for attempt in range(1, 5):
            assert await h.send() is True
            row = h.text_row(shop)
            assert (row["status"], row["attempts"]) == ("pending", attempt)
            assert row["last_error"] == "telegram_http_500"
            h.make_due(shop)
        assert await h.send() is True
        row = h.text_row(shop)
        assert (row["status"], row["attempts"]) == ("dead", 5)
        [(_, _, error)] = h.dead_letters(shop)
        assert error == {"error_code": "telegram_http_500", "retryable": False}

        # A stale text row is still put back until its fifth attempt, never skipped.
        shop2 = _seed_shop(h.owner_dsn)
        await h.write(shop2, "text")
        h.owner(
            "UPDATE outbound_deliveries SET status = 'delivering', attempts = 3,"
            " locked_at = now() - interval '1 hour' WHERE client_id = %s",
            shop2.client_id,
        )
        assert await h.worker.reap() == 1
        assert h.text_row(shop2)["status"] == "pending"


@requires_docker
async def test_bot_token_is_never_logged_or_stored(
    pg_container: object,
    app_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with harness(pg_container, app_dsn, monkeypatch) as h:
        shop = _seed_shop(h.owner_dsn)
        await h.put_picture(shop, "payment_qr")
        await h.write(shop, "image", "text")

        def broken(request: httpx.Request) -> httpx.Response:
            # httpx puts the request URL, and so the token, into some transport errors.
            raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

        h.handler[0] = broken
        with structlog.testing.capture_logs() as logs:
            assert await h.send() is True
            h.make_due(shop)
            assert await h.send() is True
        _assert_skipped(h, shop, "picture_failed", attempts=MAX_PICTURE_ATTEMPTS)
        stored = json.dumps(
            [h.deliveries(shop), h.attempts(shop), h.dead_letters(shop), h.notices(shop)],
            default=str,
        )
        out = capsys.readouterr()
        for text in (json.dumps(logs, default=str), caplog.text, out.out, out.err, stored):
            assert BOT_TOKEN not in text
            assert "synthetic-picture-bot-token" not in text
        assert any(log.get("error_code") == "picture_failed" for log in logs)
