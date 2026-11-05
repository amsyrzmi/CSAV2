"""scripts.tiktok_probe: structure only, never values; synthetic data and a mocked TikTok only."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import string
from datetime import UTC, datetime, timedelta
from typing import Any, LiteralString
from uuid import uuid4

import httpx
import psycopg
import pytest
from csa.agents.ecommerce.authorization import PRODUCT_SCOPES
from csa.agents.ecommerce.grants import create_verified_grant
from csa.agents.ecommerce.storage import create_verified_shop_binding
from csa.marketplace import processor as processor_module
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from scripts import tiktok_probe
from scripts.tiktok_probe import (
    ACCEPTED_EMPTY,
    Expect,
    _plan,
    _ShapeCapture,
    compare_shape,
    expected_paths,
    observe,
    redact_line,
    report_lines,
    structure_lines,
)

from tests.conftest import requires_docker
from tests.test_console_identity_config import configured
from tests.test_ecommerce_grants import seed
from tests.test_ecommerce_storage import decrypt, encrypt
from tests.test_tiktok_product_reads import product_payload, sku_payload
from tests.test_tiktok_snapshots import extended_order
from tests.test_tiktok_token_refresh import (
    APP_KEY,
    APP_SECRET,
    CIPHER,
    OLD_ACCESS,
    OLD_REFRESH,
    OLD_SCOPES,
    connected,
    credential_cell,
    respond,
)

pytest_plugins = ["tests.test_ecommerce_grants"]

# Made-up buyer data the probe must never print.
NAME = "Synthetic Buyer Nur Aminah"
SHORT_NAME = "Nur Aminah"
PHONE = "(+60)12-345 6789"
SPACED_PHONE = "012-345 6789"
ADDRESS = "7 Jalan Contoh, Taman Sintetik"
EMAIL = "buyer.synthetic@example.test"
TOKEN = "SYNTHETIC-TOKEN-QWxhZGRpbjpvcGVuIHNlc2FtZQ"
JWT = "eyJhbGciOiJIUzI1NiJ9abcDEF123"
CARD = "Visa 4111 1111"
URL = "https://example.invalid/synthetic/receipt.png"
BUYER_ID = "7012345678901234567"
ORDER_ID = "SYNTH-ORDER-PRIVATE-77"
PRODUCT_ID = "1729384756102938475"
PRODUCT_TITLE = "Synthetic Private Mug Title"
PAGE_TOKEN = "SYNTHETIC-PAGE-TOKEN-NEXT"
MESSAGE = "SYNTHETIC-PROVIDER-MESSAGE-TEXT"
LONG_STATUS = "AWAITING_" + "X" * 45
DYNAMIC_KEYS = ("aminah_88", "MUG_RED_L", "Aminah", "buyer7012", "nur_aminah_2b")
DEEP_VALUE = "SYNTHETIC-DEEP-PRIVATE-VALUE"
SECRETS = (
    NAME,
    SHORT_NAME,
    PHONE,
    SPACED_PHONE,
    ADDRESS,
    EMAIL,
    TOKEN,
    JWT,
    CARD,
    URL,
    BUYER_ID,
    ORDER_ID,
    PRODUCT_ID,
    PRODUCT_TITLE,
    PAGE_TOKEN,
    MESSAGE,
    LONG_STATUS,
    DEEP_VALUE,
    *DYNAMIC_KEYS,
)
# Credentials and request details of the mocked read: never in the output either.
CREDENTIALS = (CIPHER, APP_KEY, APP_SECRET, OLD_ACCESS, OLD_REFRESH)


def synthetic_order() -> dict[str, Any]:
    """A nested made-up order; its line items lack sale_price, so our parser rejects it."""
    return {
        "id": ORDER_ID,
        "status": "AWAITING_SHIPMENT",
        "create_time": 1_762_000_000,
        "user_id": BUYER_ID,
        "buyer_email": EMAIL,
        "payment_method_name": CARD,
        "payment": {"total_amount": "12.3400", "currency": "MYR", "sub_total": "10.0000"},
        "recipient_address": {
            "name": NAME,
            "phone_number": PHONE,
            "full_address": ADDRESS,
            # Allow-listed key names in positions our models do not read.
            "status": SHORT_NAME,
            "phone": {"status": SPACED_PHONE, "currency": JWT},
            "district_info": [{"address_level_name": "State", "address_name": ADDRESS}],
        },
        "line_items": [
            {
                "id": "SYNTH-LINE-1",
                "sku_id": "SYNTH-SKU-1",
                "product_name": "Synthetic mug",
                "sku_image": URL,
                "price": {"amount": "6.1700", "currency": JWT},
            }
        ],
        "packages": [{"id": BUYER_ID, "shipping_provider_name": "Synthetic Courier"}],
        "buyer_map": {key: SHORT_NAME for key in DYNAMIC_KEYS},
        "recent_names": [SHORT_NAME, SPACED_PHONE],
        "cancel_token": TOKEN,
    }


def synthetic_body() -> dict[str, Any]:
    return {
        "code": 0,
        "message": MESSAGE,
        "request_id": BUYER_ID,
        "data": {"orders": [synthetic_order(), synthetic_order()], "next_page_token": PAGE_TOKEN},
    }


def assert_no_values(text: str, *more: str) -> None:
    for value in (*SECRETS, *more):
        assert value not in text
    assert "1762000000" not in text


# -- pure functions ----------------------------------------------------------------------------


def test_structure_shows_names_and_types_never_values() -> None:
    text = "\n".join(structure_lines(observe(synthetic_body())))
    assert_no_values(text)
    assert "(body): object(4 keys)" in text
    assert "code: int" in text
    assert "message: str(len=" in text
    assert "data.orders: array of 2" in text
    assert "data.orders[].recipient_address.phone_number: str(len=16)" in text
    assert "data.orders[].recipient_address.district_info[].address_name: str(len=" in text
    assert "data.orders[].buyer_email: str(len=" in text
    assert "data.orders[].create_time: int(unix-seconds-like)" in text
    assert "data.orders[].line_items[].price: object(2 keys)" in text
    assert "data.orders[].payment.total_amount: str(len=7, decimal)" in text
    assert "data.orders[].recent_names: array of 2" in text
    assert "data.orders[].recent_names[]: str(len=10)" in text
    # Values only at the exact paths our models read, each fitting its pattern.
    assert 'data.orders[].status: str = "AWAITING_SHIPMENT"' in text
    assert 'data.orders[].payment.currency: str = "MYR"' in text
    assert "Synthetic Courier" not in text
    assert "data.orders[].packages[].shipping_provider_name: str(len=17)" in text
    # The same key names anywhere else show no value.
    assert "data.orders[].recipient_address.status: str(len=10)" in text
    assert "data.orders[].recipient_address.phone.status: str(len=12)" in text
    assert "data.orders[].recipient_address.phone.currency: str(len=" in text
    assert "data.orders[].line_items[].price.currency: str(len=" in text
    assert "data.orders[].payment_method_name: str(len=14)" in text
    assert "Synthetic mug" not in text


@pytest.mark.parametrize(
    ("path_value", "line"),
    [
        ({"status": SHORT_NAME}, "data.orders[].status: str = <redacted>"),
        ({"status": SPACED_PHONE}, "data.orders[].status: str = <redacted>"),
        ({"status": LONG_STATUS}, "data.orders[].status: str = <redacted>"),
        ({"status": 12345}, "data.orders[].status: int = <redacted>"),
        ({"status": 7}, "data.orders[].status: int = 7"),
        ({"payment": {"currency": JWT}}, "data.orders[].payment.currency: str = <redacted>"),
        ({"payment": {"currency": "myr"}}, "data.orders[].payment.currency: str = <redacted>"),
        (
            {"packages": [{"shipping_provider_name": "J&T Express 2"}]},
            "data.orders[].packages[].shipping_provider_name: str(len=13)",
        ),
        ({"status": "ROW_TOKEN_ABC123SECRET"}, "data.orders[].status: str = <redacted>"),
    ],
)
def test_allow_listed_paths_print_only_values_fitting_their_pattern(
    path_value: dict[str, Any], line: str
) -> None:
    text = "\n".join(structure_lines(observe({"data": {"orders": [path_value]}})))
    assert line in text
    assert_no_values(text, "J&T Express 2", "myr", "12345")


def test_dynamic_keys_and_maps_are_masked() -> None:
    letters = string.ascii_lowercase
    big_map = {f"seller_{a}{b}": 1 for a in letters for b in letters[:4]}  # 104 schema-like keys
    body = {"data": {"buyer_map": {key: 1 for key in DYNAMIC_KEYS}, "big": big_map}}
    seen = observe(body)
    text = "\n".join(structure_lines(seen))
    assert_no_values(text, *big_map)
    for index in range(1, len(DYNAMIC_KEYS) + 1):
        assert f"data.buyer_map.<key#{index}>: int" in text
    assert "data.big: object(104 keys)" in text
    assert "data.big.<key#104>: int" in text
    # Schema-looking words still show, a single trailing digit included.
    shown = observe({"line_items": 1, "address_line1": 2, "Line_Items": 3})
    assert "line_items" in shown and "address_line1" in shown and "Line_Items" not in shown


def test_nothing_below_the_depth_limit_is_examined() -> None:
    body: Any = {"deep_value": DEEP_VALUE, "note": DEEP_VALUE}
    for _ in range(40):
        body = {"level": body}
    text = "\n".join(structure_lines(observe(body)))
    assert_no_values(text)
    assert "deep_value" not in text


def test_mixed_and_empty_arrays() -> None:
    seen = observe({"a": [1, "x", None], "b": []})
    assert seen["a"].label == "array of 3 (mixed: integer, null, string)"
    assert seen["a[]"].kind == "integer"
    assert seen["b"].label == "array of 0" and seen["b"].length == 0
    assert "b[]" not in seen


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("data.orders[].note: str(len=4)", "data.orders[].note: str(len=4)"),
        ("data.x: contact buyer@example.test", "data.x: <redacted>"),
        ("data.x: 123456789", "data.x: <redacted>"),
        ("data.x: 12345678", "data.x: 12345678"),
        ("data.x: see https://example.invalid/a", "data.x: <redacted>"),
        ("data.x: see www.example.invalid", "data.x: <redacted>"),
        ("data.7012345678901234567: str(len=3)", "<redacted>"),
        ("plain line with ftp://example.invalid", "<redacted>"),
        ("plain line", "plain line"),
    ],
)
def test_redaction_scan(line: str, expected: str) -> None:
    assert redact_line(line) == expected


def test_compare_shape_marks_present_missing_wrong_type_and_not_seen() -> None:
    observed = observe(
        {"data": {"orders": [{"id": 5, "status": "X", "payment": "12.00"}], "skus": []}}
    )
    verdicts = compare_shape(
        observed,
        [
            Expect("data.orders[].status", ("string",), True),
            Expect("data.orders[].id", ("string",), True),
            Expect("data.orders[].line_items", ("array",), True),
            Expect("data.orders[].payment.total_amount", ("string",), True),
            Expect("data.next_page_token", ("string",), False),
            Expect("data.skus[].id", ("string",), True),
            Expect("data.skus", ("array",), True),
        ],
    )
    got = {(v.path, v.verdict, v.note) for v in verdicts}
    assert got == {
        ("data.orders[].status", "present", None),
        ("data.orders[].id", "wrong type", None),
        ("data.orders[].line_items", "MISSING", None),
        ("data.orders[].payment.total_amount", "not seen", "(data.orders[].payment is string)"),
        ("data.next_page_token", "MISSING", None),
        ("data.skus[].id", "not seen", "(its array was empty)"),
        ("data.skus", "present", None),
    }
    wrong = next(v for v in verdicts if v.verdict == "wrong type")
    assert (wrong.got, wrong.want) == ("integer", "string")


def test_explicit_null_packages_is_wrong_type_with_its_note() -> None:
    body = {"code": 0, "data": {"orders": [{**extended_order(), "packages": None}]}}
    verdict = next(
        v
        for v in compare_shape(observe(body), expected_paths("orders"))
        if v.path == "data.orders[].packages"
    )
    assert (verdict.verdict, verdict.note) == ("wrong type", "(null fails parsing)")


def bare_price_product() -> dict[str, Any]:
    product = product_payload()
    sku = sku_payload()
    sku["price"], sku["currency"] = "19.9000", "MYR"
    product["skus"] = [sku]
    return product


@pytest.mark.parametrize(
    ("mode", "body"),
    [
        ("orders", {"code": 0, "data": {"orders": [extended_order()], "next_page_token": ""}}),
        ("orders", {"code": 0, "data": {"orders": [extended_order()], "next_page_token": None}}),
        ("order", {"code": 0, "data": {"orders": [extended_order()]}}),
        ("products", {"code": 0, "data": {"products": [product_payload()]}}),
        ("product", {"code": 0, "data": product_payload()}),
        ("product", {"code": 0, "data": bare_price_product()}),
        ("orders", {"code": 0, "data": {"total_count": 0}}),
        ("products", {"code": 0, "data": {"total_count": 0, "next_page_token": ""}}),
    ],
)
def test_expected_paths_match_what_our_models_accept(mode: str, body: dict) -> None:
    """Bodies our models parse show no required path missing or mistyped."""
    verdicts = compare_shape(observe(body), expected_paths(mode))
    bad = [v for v in verdicts if v.required and v.verdict in ("MISSING", "wrong type")]
    assert bad == []


@pytest.mark.parametrize(
    ("mode", "data", "verdict"),
    [
        ("orders", {"total_count": 0}, ACCEPTED_EMPTY),
        ("products", {"total_count": 0}, ACCEPTED_EMPTY),
        ("orders", {"total_count": 3}, "MISSING"),
        ("orders", {"total_count": "0"}, "MISSING"),
        ("orders", {"total_count": False}, "MISSING"),
        ("orders", {"total_count": 0.0}, "MISSING"),
        ("orders", {}, "MISSING"),
        ("order", {"total_count": 0}, "MISSING"),
    ],
)
def test_missing_list_is_accepted_only_with_integer_zero_total(
    mode: str, data: dict, verdict: str
) -> None:
    key = "data.products" if mode == "products" else "data.orders"
    verdicts = compare_shape(observe({"code": 0, "data": data}), expected_paths(mode))
    assert next(v for v in verdicts if v.path == key).verdict == verdict


def test_total_count_value_is_shown() -> None:
    text = "\n".join(structure_lines(observe({"data": {"total_count": 0}})))
    assert "data.total_count: int = 0" in text


# -- report_lines and the capture --------------------------------------------------------------


def capture_of(status: int | None, body: bytes | None, *, too_large: bool = False):
    capture = _ShapeCapture(httpx.MockTransport(lambda _: httpx.Response(204)))
    capture.status, capture.body, capture.too_large = status, body, too_large
    return capture


def orders_plan():
    return _plan("orders", None, datetime.now(UTC))


def test_report_for_a_provider_error_never_shows_its_message() -> None:
    body = json.dumps({"code": 105001, "message": MESSAGE, "data": None}).encode()
    lines, code = report_lines(orders_plan(), capture_of(200, body), "provider_rejected")
    text = "\n".join(lines)
    assert code == 1
    assert "provider code: 105001" in text and "our parser: provider_rejected" in text
    assert_no_values(text)


def test_report_for_a_non_json_body_shows_only_its_size() -> None:
    body = f"<html>{NAME} {PHONE}</html>".encode()
    lines, code = report_lines(orders_plan(), capture_of(502, body), "http_error")
    text = "\n".join(lines)
    assert code == 1 and f"body: not JSON ({len(body)} bytes)" in text
    assert_no_values(text)


def test_report_for_a_read_that_failed_mid_body() -> None:
    lines, code = report_lines(orders_plan(), capture_of(200, None), "transport_error")
    assert code == 1 and "body: not read" in lines


@pytest.mark.asyncio
async def test_capture_skips_a_body_over_the_limit_and_never_reads_the_shops_reply() -> None:
    huge = json.dumps({"code": 0, "data": {"name": NAME * 200_000}}).encode()
    assert len(huge) > 4 * 1024 * 1024

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/authorization/202309/shops":
            return respond({"code": 0, "data": {"shops": [{"id": "S", "cipher": CIPHER}]}})
        return httpx.Response(200, content=huge)

    capture = _ShapeCapture(httpx.MockTransport(handler))
    shops = httpx.Request("GET", "https://tiktok.invalid/authorization/202309/shops")
    await capture.handle_async_request(shops)
    assert capture.status is None and capture.body is None
    read = httpx.Request("POST", "https://tiktok.invalid/order/202309/orders/search")
    await capture.handle_async_request(read)
    assert capture.too_large and capture.body is None
    lines, code = report_lines(orders_plan(), capture, "ok")
    text = "\n".join(lines)
    assert code == 1 and "not examined" in text
    assert_no_values(text)


# -- main: refusals without a database ---------------------------------------------------------


def no_network(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
        seen.append(request)
        raise AssertionError("the probe must not call TikTok here")

    monkeypatch.setattr(
        processor_module, "_new_read_transport", lambda: httpx.MockTransport(handler)
    )
    return seen


@pytest.mark.parametrize(
    "argv",
    [
        ["order"],
        ["orders", "SYNTH-1"],
        ["everything"],
        ["orders", "--store-id", str(uuid4())],
        ["orders", "--client-id", "not-a-uuid"],
    ],
)
def test_bad_arguments_are_refused(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    seen = no_network(monkeypatch)
    monkeypatch.setattr(tiktok_probe, "get_settings", lambda: pytest.fail("no settings needed"))
    assert tiktok_probe.main(argv) == 2
    assert seen == []


def test_tiktok_off_is_refused_and_logging_is_restored(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen = no_network(monkeypatch)
    monkeypatch.setattr(tiktok_probe, "get_settings", lambda: configured())
    before = logging.root.manager.disable
    assert tiktok_probe.main(["orders"]) == 2
    assert logging.root.manager.disable == before
    assert "TikTok is off" in capsys.readouterr().err
    assert seen == []


# -- main end to end: migrated Postgres, a connected synthetic store, mocked TikTok -----------


def probe_settings(app_dsn: str, app_id: str):
    # On the gateway LG_DATABASE_URL is a csa_app DSN too; the probe only lists companies on it.
    return configured(
        DATABASE_URL=app_dsn,
        LG_DATABASE_URL=app_dsn,
        IDENTITY_DATABASE_URL=None,
        TIKTOK_APP_KEY=APP_KEY,
        TIKTOK_APP_SECRET=APP_SECRET,
        TIKTOK_SERVICE_ID="SYNTHETIC-SERVICE-ID",
        TIKTOK_APP_ID=app_id,
    )


class FakeTikTok:
    def __init__(self, shop_id: str, path: str, body: dict[str, Any]) -> None:
        self.shop_id = shop_id
        self.path = path
        self.body = body
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/authorization/202309/shops":
            return respond({"code": 0, "data": {"shops": [{"id": self.shop_id, "cipher": CIPHER}]}})
        assert request.url.path == self.path
        assert request.url.params["shop_cipher"] == CIPHER
        return respond(self.body)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeTikTok:
        monkeypatch.setattr(
            processor_module, "_new_read_transport", lambda: httpx.MockTransport(self.handler)
        )
        return self

    def leaks(self) -> list[str]:
        """Everything about the signed requests that must never be printed."""
        out = [*CREDENTIALS, self.shop_id]
        for request in self.requests:
            out += [str(request.url), request.url.query.decode(), request.url.params["sign"]]
            out += [request.headers.get("x-tts-access-token", "")]
        return [value for value in out if value]


async def only_active(migrated_dsn: str, *clients: Any) -> None:
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        await conn.execute(
            "UPDATE clients SET status = "
            "CASE WHEN id = ANY(%s) THEN 'active' ELSE 'onboarding' END",
            (list(clients),),
        )


async def count(migrated_dsn: str, sql: LiteralString, *params: Any) -> int:
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as conn:
        row = await (await conn.execute(sql, params)).fetchone()
    assert row is not None
    return int(row[0])


def run_main(argv: list[str]) -> int:
    """main() runs its own event loop, so the async tests call it from a worker thread."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(tiktok_probe.main, argv).result()


def use(monkeypatch: pytest.MonkeyPatch, app_dsn: str, app_id: str) -> None:
    settings = probe_settings(app_dsn, app_id)
    monkeypatch.setattr(tiktok_probe, "get_settings", lambda: settings)
    monkeypatch.setattr(tiktok_probe, "decrypt", decrypt)


async def connected_without_products(owner: str, pool: AsyncConnectionPool):
    """Like ``connected``, but the grant lacks the product permission."""
    seeded = await seed(owner)
    record_id = seeded.context.binding.record_id
    async with await psycopg.AsyncConnection.connect(owner) as conn:
        await conn.execute(
            "UPDATE channel_credentials SET access_token=%s, refresh_token=%s, "
            "expires_at=%s WHERE id=%s",
            (
                credential_cell(OLD_ACCESS, "access_token", record_id),
                credential_cell(OLD_REFRESH, "refresh_token", record_id),
                datetime.now(UTC) + timedelta(hours=1),
                record_id,
            ),
        )
    grant = seeded.model_copy(
        update={
            "context": seeded.context.model_copy(update={"credential_generation": 2}),
            "scopes": OLD_SCOPES - PRODUCT_SCOPES,
        }
    )
    await create_verified_grant(pool, grant)
    await create_verified_shop_binding(pool, grant, SecretStr(CIPHER), encrypt=encrypt)
    return grant


@requires_docker
@pytest.mark.asyncio
async def test_orders_probe_prints_structure_and_our_verdicts_never_values(
    migrated_dsn: str,
    app_dsn: str,
    pool: AsyncConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    grant = await connected(migrated_dsn, pool)
    binding = grant.context.binding
    await only_active(migrated_dsn, binding.client_id)
    use(monkeypatch, app_dsn, grant.context.app_id)
    fake = FakeTikTok(binding.shop_id, "/order/202309/orders/search", synthetic_body())
    fake.install(monkeypatch)
    before = await count(migrated_dsn, "SELECT count(*) FROM ecommerce_read_operations")

    # No flags: the single connected store among the active companies is chosen.
    assert run_main(["orders"]) == 0

    out = capsys.readouterr()
    printed = out.out + out.err
    assert_no_values(printed, *fake.leaks())
    assert "request: POST /order/202309/orders/search (page size 5" in out.out
    assert "http status: 200" in out.out
    assert "provider code: 0" in out.out
    assert "page token: present (value not shown)" in out.out
    assert "our parser: invalid_response" in out.out
    for name in ("recipient_address.phone_number", "buyer_email", "user_id", "cancel_token"):
        assert f"data.orders[].{name}: str(len=" in out.out
    assert 'data.orders[].status: str = "AWAITING_SHIPMENT"' in out.out
    assert "OUR MODEL SAYS" in out.out
    assert "present    required data.orders[].id" in out.out
    assert "MISSING    required data.orders[].line_items[].sale_price" in out.out
    assert "MISSING    optional data.orders[].paid_time" in out.out
    # One signed read after the fresh authorized-shops check, sent the way the sync sends it.
    paths = [r.url.path for r in fake.requests]
    assert paths == ["/authorization/202309/shops", "/order/202309/orders/search"]
    search = fake.requests[1]
    assert search.method == "POST" and search.url.params["page_size"] == "5"
    window = json.loads(search.content)
    assert window["update_time_lt"] - window["update_time_ge"] == 90 * 86400
    assert "sign" in search.url.params and "access_token" not in search.url.params
    # Read-only: no order copied; only the one fence row every sync read adds.
    orders = await count(
        migrated_dsn, "SELECT count(*) FROM orders WHERE client_id=%s", binding.client_id
    )
    assert orders == 0
    after = await count(migrated_dsn, "SELECT count(*) FROM ecommerce_read_operations")
    assert after == before + 1


@requires_docker
@pytest.mark.asyncio
async def test_product_probe_uses_the_path_template_and_never_prints_the_id(
    migrated_dsn: str,
    app_dsn: str,
    pool: AsyncConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    grant = await connected(migrated_dsn, pool)
    binding = grant.context.binding
    use(monkeypatch, app_dsn, grant.context.app_id)
    product = {**product_payload(PRODUCT_ID), "title": PRODUCT_TITLE, "owner_email": EMAIL}
    fake = FakeTikTok(binding.shop_id, f"/product/202309/products/{PRODUCT_ID}", {"code": 0})
    fake.body = {"code": 0, "message": MESSAGE, "data": product}
    fake.install(monkeypatch)

    assert run_main(["product", PRODUCT_ID, "--client-id", str(binding.client_id)]) == 0

    out = capsys.readouterr()
    assert_no_values(out.out + out.err, *fake.leaks())
    assert "request: GET /product/202309/products/{product_id}" in out.out
    assert "our parser: ok" in out.out
    assert "page token" not in out.out
    assert 'data.status: str = "ACTIVATE"' in out.out
    assert 'data.skus[].price.currency: str = "MYR"' in out.out
    assert "data.title: str(len=" in out.out
    assert "present    required data.skus[].inventory[].quantity" in out.out
    assert [r.method for r in fake.requests] == ["GET", "GET"]


@requires_docker
@pytest.mark.asyncio
async def test_product_probe_without_the_product_permission_reads_nothing(
    migrated_dsn: str,
    app_dsn: str,
    pool: AsyncConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    grant = await connected_without_products(migrated_dsn, pool)
    binding = grant.context.binding
    use(monkeypatch, app_dsn, grant.context.app_id)
    fake = FakeTikTok(binding.shop_id, f"/product/202309/products/{PRODUCT_ID}", {"code": 0})
    fake.install(monkeypatch)

    assert run_main(["product", PRODUCT_ID, "--client-id", str(binding.client_id)]) == 1

    out = capsys.readouterr()
    assert out.out == ""
    assert out.err.splitlines()[-1] == "failed: insufficient_scope (no response to examine)"
    assert_no_values(out.err, *fake.leaks())
    assert fake.requests == []


@requires_docker
@pytest.mark.asyncio
async def test_two_connected_stores_are_refused_without_a_read(
    migrated_dsn: str,
    app_dsn: str,
    pool: AsyncConnectionPool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    first = await connected(migrated_dsn, pool)
    second = await connected(migrated_dsn, pool)
    await only_active(
        migrated_dsn, first.context.binding.client_id, second.context.binding.client_id
    )
    use(monkeypatch, app_dsn, first.context.app_id)
    seen = no_network(monkeypatch)

    assert run_main(["orders"]) == 2
    err = capsys.readouterr().err
    assert "2 connected TikTok stores" in err and "do not paste" in err
    assert f"--client-id {first.context.binding.client_id}" in err
    assert first.context.binding.shop_id not in err
    assert seen == []
    # A company with no connected store is refused too.
    assert run_main(["orders", "--client-id", str(uuid4())]) == 2
    assert "no connected TikTok store" in capsys.readouterr().err
