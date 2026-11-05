"""Show the SHAPE of one TikTok Shop response, never its values: a read-only diagnostic.

Run on the staging host (section 12.12 of ``deploy/STAGING_PIPELINE.md``)::

    docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe orders
    docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe products
    docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe order <order_id>
    docker exec csa-staging-gateway-1 python -m scripts.tiktok_probe product <product_id>

Add ``--client-id <uuid>`` (and ``--store-id <uuid>``) when there is not exactly one connected
store among the active and pilot companies.

One read per run, through the same fenced path the sync uses (``processor.fenced_read``:
``begin_read`` then ``read_verified`` with the stored-credential resolver, so a fresh
authorized-shops check, then the read, signed the same way, on the sync's no-retry transport).
The probe wraps that transport and keeps the body of the one read it asked for, before any of our
parsing; the authorized-shops response (it carries the shop cipher) is passed through unread.

Only the body's structure is printed: key names, JSON types, array lengths and string lengths.
Values are printed only at a few exact paths (``ENUM_PATHS``: order and product status,
currency, carrier name, and ``COUNT_PATHS``: the search's total count), and only when the value
fits that path's strict pattern; anything else there prints as ``<redacted>``. Key names are
printed only when they look like schema words (lower-case words joined by ``_``, at most one
trailing digit) and their object has at most ``_MAP_KEYS`` keys; other keys print as
``<key#N>``. A last scan blanks any line that still looks like an email, a long digit run or a
URL. Nothing is logged.

Left-over risk: a key that is a plain lower-case word (a map keyed by, say, a seller's own
lower-case words) cannot be told from a schema word, so such a key would be printed.

It writes nothing to TikTok, orders or listings. The only rows it adds are the single-use
read-operation fence rows (``ecommerce_read_operations``) that every sync read adds.

Exit codes: 0 printed; 2 refused (TikTok off, no or ambiguous store, bad arguments); 1 provider
or internal failure (a safe code or an exception class name only, never provider text).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from csa.agents.ecommerce.adapters.tiktok_shop import TikTokReadError
from csa.agents.ecommerce.app_loader import build_app_loader
from csa.agents.ecommerce.authorization import TikTokAuthorizationError
from csa.agents.ecommerce.dispatch import TikTokReadRequest
from csa.core import db
from csa.core.crypto import decrypt
from csa.core.settings import Settings, get_settings
from csa.core.tenancy import tx
from csa.marketplace import jobs, processor
from csa.marketplace.jobs import SyncStore
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

PAGE_SIZE = 5
REDACTED = "<redacted>"

_STATUS = re.compile(r"(?=.{1,40}\Z)[A-Z]+(?:_[A-Z]+){0,5}")
_CURRENCY = re.compile(r"[A-Z]{3}")
# The only paths whose value is printed, each with the strict pattern its value must fit.
# Paths are the ones our models read; an order detail uses the same ``data.orders[]`` paths.
ENUM_PATHS: dict[str, re.Pattern[str]] = {
    "data.orders[].status": _STATUS,
    "data.orders[].payment.currency": _CURRENCY,
    "data.products[].status": _STATUS,
    "data.products[].skus[].price.currency": _CURRENCY,
    "data.products[].skus[].currency": _CURRENCY,
    "data.status": _STATUS,
    "data.skus[].price.currency": _CURRENCY,
    "data.skus[].currency": _CURRENCY,
}
# Status paths may also carry a small number code: shown as ``int = N`` when 0..999.
_INT_ENUM_PATHS = frozenset({"data.orders[].status", "data.products[].status", "data.status"})
# Counts: a non-negative integer below a million is shown as ``int = N``.
COUNT_PATHS = frozenset({"data.total_count"})

_DECIMAL_TEXT = re.compile(r"-?\d{1,30}(\.\d{1,30})?")
_KEY_NAME = re.compile(r"[a-z]+(?:_[a-z]+)*[0-9]?")
_MAX_KEY = 64
# An object with more keys than this is a map (keyed by data), not a schema: every key is masked.
# TikTok's own order object is large, so the bound is above any schema we expect to see.
_MAP_KEYS = 100
_SAFE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_LONG_DIGITS = re.compile(r"\d{9,}")
_URL = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://|\bwww\.")
_SHOPS_PATH = "/authorization/202309/shops"
_MAX_DEPTH = 24
_MAX_LINES = 600
_MAX_BODY = 4 * 1024 * 1024


# -- structure ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Seen:
    """One JSON path as observed: its JSON kind, a value-free label, and an array's length.

    ``zero`` marks an integer 0 (``data.total_count`` 0 makes a missing list an empty page).
    """

    kind: str  # object | array | string | integer | float | bool | null
    label: str
    length: int | None = None
    zero: bool = False


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "unknown"


def _label(value: Any, path: str) -> str:
    kind = _kind(value)
    if kind == "object":
        return f"object({len(value)} keys)"
    if kind == "array":
        kinds = sorted({_kind(item) for item in value})
        mixed = f" (mixed: {', '.join(kinds)})" if len(kinds) > 1 else ""
        return f"array of {len(value)}{mixed}"
    if kind == "string":
        pattern = ENUM_PATHS.get(path)
        if pattern is not None:
            return f'str = "{value}"' if pattern.fullmatch(value) else f"str = {REDACTED}"
        # Whether a string reads as a number matters for our money fields; the digits do not.
        hint = ", decimal" if _DECIMAL_TEXT.fullmatch(value) else ""
        return f"str(len={len(value)}{hint})"
    if kind == "integer":
        if path in _INT_ENUM_PATHS:
            return f"int = {value}" if 0 <= value < 1000 else f"int = {REDACTED}"
        if path in COUNT_PATHS:
            return f"int = {value}" if 0 <= value < 10**6 else f"int = {REDACTED}"
        if 10**9 <= value < 10**10:
            return "int(unix-seconds-like)"
        if 10**12 <= value < 10**13:
            return "int(unix-millis-like)"
        return "int"
    return kind


def _shown_key(name: Any) -> str | None:
    if isinstance(name, str) and len(name) <= _MAX_KEY and _KEY_NAME.fullmatch(name):
        return name
    return None


def observe(body: Any) -> dict[str, Seen]:
    """Every JSON path of ``body`` with a value-free description, in document order.

    Paths join keys with ``.``; ``[]`` is an array's first element (arrays are described by
    their first element and their length). The body itself is the path ``""``. Keys that do not
    look like schema words, and every key of an object with more than ``_MAP_KEYS`` keys, are
    shown as ``<key#N>``. Nothing deeper than ``_MAX_DEPTH`` levels is examined.
    """
    seen: dict[str, Seen] = {}

    def walk(value: Any, path: str, depth: int) -> None:
        kind = _kind(value)
        seen[path] = Seen(
            kind,
            _label(value, path),
            len(value) if kind == "array" else None,
            zero=kind == "integer" and value == 0,
        )
        if depth >= _MAX_DEPTH:
            return
        if kind == "object":
            is_map = len(value) > _MAP_KEYS
            masked = 0
            for name, child in value.items():
                shown = None if is_map else _shown_key(name)
                if shown is None:
                    masked += 1
                    shown = f"<key#{masked}>"
                walk(child, f"{path}.{shown}" if path else shown, depth + 1)
        elif kind == "array" and value:
            walk(value[0], f"{path}[]", depth + 1)

    walk(body, "", 0)
    return seen


def redact_line(line: str) -> str:
    """The last guard: a line that looks like it carries an email, a long number or a URL.

    A suspicious path blanks the whole line; a suspicious description blanks only that part.
    """

    def suspicious(text: str) -> bool:
        return bool(_EMAIL.search(text) or _LONG_DIGITS.search(text) or _URL.search(text))

    path, sep, rest = line.partition(": ")
    if not sep:
        return REDACTED if suspicious(line) else line
    if suspicious(path):
        return REDACTED
    return f"{path}: {REDACTED}" if suspicious(rest) else line


def structure_lines(seen: Mapping[str, Seen]) -> list[str]:
    lines = [redact_line(f"{path or '(body)'}: {item.label}") for path, item in seen.items()]
    if len(lines) > _MAX_LINES:
        hidden = len(lines) - _MAX_LINES
        lines = [*lines[:_MAX_LINES], f"... {hidden} more paths not shown"]
    return lines


# -- our model's expectations ------------------------------------------------------------------


@dataclass(frozen=True)
class Expect:
    """A provider path one of our models reads, and the JSON kinds it accepts.

    ``empty_if_total_zero``: a missing list is accepted as an empty page when the sibling
    ``total_count`` is the integer 0 (the adapter's ``_explicit_empty``). ``note`` is printed
    with a wrong type.
    """

    path: str
    kinds: tuple[str, ...]
    required: bool
    empty_if_total_zero: bool = False
    note: str | None = None


@dataclass(frozen=True)
class Verdict:
    path: str
    required: bool
    verdict: str  # present | MISSING | wrong type | not seen | absent (total_count 0, accepted)
    got: str | None = None
    want: str | None = None
    note: str | None = None


ACCEPTED_EMPTY = "absent (total_count 0, accepted)"


def _parent(path: str) -> str | None:
    if not path:
        return None
    if path.endswith("[]"):
        return path[:-2]
    return path.rsplit(".", 1)[0] if "." in path else ""


def compare_shape(observed: Mapping[str, Seen], expected: Iterable[Expect]) -> list[Verdict]:
    """Mark each expected path present, MISSING or wrong type against the observed shape.

    A path under an empty array, or under a value that is not an object, is ``not seen``: there
    was nothing to look in (the parent's own line says whether that is wrong). A list that
    may be left out of an empty search page is ``absent (total_count 0, accepted)`` exactly
    when the sibling ``total_count`` is the integer 0.
    """
    expected = list(expected)

    def empty_page(path: str) -> bool:
        parent = _parent(path)
        total = observed.get(f"{parent}.total_count" if parent else "total_count")
        return total is not None and total.kind == "integer" and total.zero

    accepted = {
        w.path
        for w in expected
        if w.empty_if_total_zero and w.path not in observed and empty_page(w.path)
    }
    verdicts: list[Verdict] = []
    for want in expected:
        found = observed.get(want.path)
        if found is not None:
            if found.kind in want.kinds:
                verdicts.append(Verdict(want.path, want.required, "present"))
            else:
                verdicts.append(
                    Verdict(
                        want.path,
                        want.required,
                        "wrong type",
                        got=found.kind,
                        want="|".join(want.kinds),
                        note=want.note,
                    )
                )
            continue
        if want.path in accepted:
            verdicts.append(Verdict(want.path, want.required, ACCEPTED_EMPTY))
            continue
        verdict, note = "MISSING", None
        ancestor = _parent(want.path)
        while ancestor is not None:
            if ancestor in accepted:
                verdict, note = "not seen", "(an empty page: total_count 0)"
                break
            above = observed.get(ancestor)
            if above is not None:
                if above.kind == "array" and above.length == 0:
                    verdict, note = "not seen", "(its array was empty)"
                elif above.kind not in ("object", "array"):
                    verdict, note = "not seen", f"({ancestor or '(body)'} is {above.kind})"
                break
            ancestor = _parent(ancestor)
        verdicts.append(Verdict(want.path, want.required, verdict, note=note))
    return verdicts


def verdict_lines(verdicts: Iterable[Verdict]) -> list[str]:
    lines = []
    for item in verdicts:
        need = "required" if item.required else "optional"
        line = f"{item.verdict:<10} {need:<8} {item.path}"
        if item.verdict == "wrong type":
            line += f"  (got {item.got}, want {item.want})"
            if item.note:
                line += f"  {item.note}"
        elif item.verdict == "not seen" and item.note:
            line += f"  {item.note}"
        lines.append(redact_line(line))
    return lines


_TEXT = ("string",)
_TEXT_OR_NULL = ("string", "null")
_DECIMAL = ("string", "integer", "float")
_DECIMAL_OR_NULL = (*_DECIMAL, "null")
_INT = ("integer",)
_TIME = ("integer", "float", "string", "null")
_OBJECT = ("object",)
_ARRAY = ("array",)
_ANY = ("object", "array", "string", "integer", "float", "bool", "null")

type _Field = tuple[str, tuple[str, ...], bool] | tuple[str, tuple[str, ...], bool, str]

# Provider names read by TikTokOrderSnapshot (tiktok_models.py, the order mapping block).
_ORDER_FIELDS: tuple[_Field, ...] = (
    ("id", _TEXT, True),
    ("status", _TEXT, True),
    ("payment", _OBJECT, True),
    ("payment.total_amount", _DECIMAL, True),
    ("payment.currency", _TEXT, True),
    ("payment.sub_total", _DECIMAL_OR_NULL, False),
    ("payment.shipping_fee", _DECIMAL_OR_NULL, False),
    ("payment.seller_discount", _DECIMAL_OR_NULL, False),
    ("line_items", _ARRAY, True),
    ("line_items[].id", _TEXT, True),
    ("line_items[].sku_id", _TEXT, True),
    ("line_items[].seller_sku", _TEXT_OR_NULL, False),
    ("line_items[].product_name", _TEXT, True),
    ("line_items[].sale_price", _DECIMAL, True),
    ("create_time", _TIME, False),
    ("update_time", _TIME, False),
    ("paid_time", _TIME, False),
    # Anything that is not an object is ignored, so every kind parses.
    ("recipient_address", _ANY, False),
    ("recipient_address.name", _TEXT_OR_NULL, False),
    ("recipient_address.phone_number", _TEXT_OR_NULL, False),
    ("recipient_address.full_address", _TEXT_OR_NULL, False),
    ("recipient_address.region_code", _TEXT_OR_NULL, False),
    # Absent is fine; an explicit null fails parsing unless an order-level tracking number
    # stands in for it (tiktok_models.py, fall_back_to_order_level_tracking).
    ("packages", _ARRAY, False, "(null fails parsing)"),
    ("packages[].tracking_number", _TEXT_OR_NULL, False),
    ("packages[].shipping_provider_name", _TEXT_OR_NULL, False),
    ("tracking_number", _TEXT_OR_NULL, False),
    ("shipping_provider", _TEXT_OR_NULL, False),
)

# Provider names read by TikTokProductSnapshot (tiktok_models.py, the product mapping block).
_PRODUCT_FIELDS: tuple[_Field, ...] = (
    ("id", _TEXT, True),
    ("title", _TEXT, True),
    ("status", _TEXT, True),
    ("main_images", ("array", "null"), False),
    ("main_images[].urls", ("array", "null"), False),
    ("main_images[].urls[]", _TEXT_OR_NULL, False),
    ("update_time", _TIME, False),
    ("skus", _ARRAY, True),
    ("skus[].id", _TEXT, True),
    ("skus[].seller_sku", _TEXT_OR_NULL, False),
    # Either an object with sale_price and currency, or a bare amount with skus[].currency.
    ("skus[].price", ("object", *_DECIMAL), True),
    ("skus[].price.sale_price", _DECIMAL, True),
    ("skus[].price.currency", _TEXT, True),
    ("skus[].currency", _TEXT, False),
    ("skus[].sales_attributes", _ARRAY, False, "(present but not a list fails parsing)"),
    ("skus[].sales_attributes[].value_name", _TEXT_OR_NULL, False),
    ("skus[].inventory", _ARRAY, True),
    ("skus[].inventory[].quantity", _INT, True),
)


def _under(prefix: str, fields: Iterable[_Field]) -> list[Expect]:
    out = []
    for field in fields:
        path, kinds, required = field[0], field[1], field[2]
        note = field[3] if len(field) == 4 else None
        out.append(Expect(f"{prefix}.{path}", kinds, required, note=note))
    return out


_ENVELOPE = [Expect("code", _INT, True), Expect("data", _OBJECT, True)]
_SEARCH = [
    Expect("data.next_page_token", _TEXT_OR_NULL, False),
    Expect("data.total_count", _INT, False),
]


def expected_paths(mode: str) -> list[Expect]:
    """What our envelope and snapshot models read, as provider paths, for one probe mode."""
    if mode == "orders":
        page = [Expect("data.orders", _ARRAY, True, empty_if_total_zero=True), *_SEARCH]
        return [*_ENVELOPE, *page, *_under("data.orders[]", _ORDER_FIELDS)]
    if mode == "order":
        page = [Expect("data.orders", _ARRAY, True)]
        return [*_ENVELOPE, *page, *_under("data.orders[]", _ORDER_FIELDS)]
    if mode == "products":
        page = [Expect("data.products", _ARRAY, True, empty_if_total_zero=True), *_SEARCH]
        return [*_ENVELOPE, *page, *_under("data.products[]", _PRODUCT_FIELDS)]
    return [*_ENVELOPE, *_under("data", _PRODUCT_FIELDS)]


# -- the read ----------------------------------------------------------------------------------


class _ShapeCapture(httpx.AsyncBaseTransport):
    """Passes every request to the sync's own transport; keeps the probed read's body only.

    The authorized-shops response carries the shop cipher: it is passed through unread.
    """

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self._inner = inner
        self.status: int | None = None
        self.body: bytes | None = None
        self.too_large = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        response = await self._inner.handle_async_request(request)
        if request.url.path == _SHOPS_PATH:
            return response
        self.status = response.status_code
        content = await response.aread()  # the client reuses this already-read content
        if len(content) > _MAX_BODY:
            self.too_large = True
        else:
            self.body = content
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass(frozen=True)
class _Plan:
    mode: str
    request: TikTokReadRequest
    describe: str


def _plan(mode: str, target: str | None, now: datetime) -> _Plan:
    """Raises ValidationError for an id the sync itself would refuse."""
    if mode == "orders":
        days = processor.SyncConfig().backfill_days
        request = TikTokReadRequest(
            kind="search",
            limit=PAGE_SIZE,
            updated_from=now - timedelta(days=days),
            updated_to=now,
        )
        text = (
            f"POST /order/202309/orders/search (page size {PAGE_SIZE}, "
            f"updated in the last {days} days)"
        )
        return _Plan(mode, request, text)
    if mode == "products":
        request = TikTokReadRequest(kind="product_search", limit=PAGE_SIZE)
        return _Plan(mode, request, f"POST /product/202309/products/search (page size {PAGE_SIZE})")
    if mode == "order":
        request = TikTokReadRequest(kind="detail", order_id=target)
        return _Plan(mode, request, "GET /order/202309/orders?ids={order_id}")
    request = TikTokReadRequest(kind="product_detail", product_id=target)
    return _Plan(mode, request, "GET /product/202309/products/{product_id}")


class _Refused(Exception):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.text = text


async def _choose_store(
    settings: Settings,
    app_pool: AsyncConnectionPool,
    client_id: UUID | None,
    store_id: UUID | None,
) -> SyncStore:
    if client_id is not None:
        async with tx(app_pool, str(client_id)) as conn:
            if store_id is not None:
                found = await jobs.get_sync_store(conn, client_id, store_id)
                stores = [found] if found is not None else []
            else:
                stores = await jobs.list_connected_stores(conn, client_id)
    else:
        # The same pool and query the worker's processor lists its active companies with.
        listing_pool = db.build_pool(str(settings.LG_DATABASE_URL), min_size=1, max_size=1)
        await listing_pool.open(wait=True)
        try:
            async with listing_pool.connection() as conn:
                clients = await db.q.fetch_all("jobs/list_active_clients.sql", conn=conn)
        finally:
            await listing_pool.close()
        stores = []
        for row in clients:
            async with tx(app_pool, str(row["id"])) as conn:
                stores.extend(await jobs.list_connected_stores(conn, row["id"]))
    if store_id is not None:
        if not stores:
            raise _Refused("no TikTok store with that id for that company")
        store = stores[0]
        if store.sync_state != "connected" or store.grant_id is None or not store.shop_id:
            raise _Refused(f"that store is not connected (state: {store.sync_state})")
        return store
    if not stores:
        raise _Refused(
            "no connected TikTok store found (only active and pilot companies are searched; "
            "pass --client-id for another company)"
        )
    if len(stores) > 1:
        listed = "\n".join(f"  --client-id {s.client_id} --store-id {s.store_id}" for s in stores)
        raise _Refused(
            f"{len(stores)} connected TikTok stores; run again with one of these. They are our "
            f"own company and store ids, not TikTok's; keep them on this host, do not paste "
            f"them:\n{listed}"
        )
    return stores[0]


def _safe_code(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(exc, (TikTokReadError, TikTokAuthorizationError)) and isinstance(code, str):
        if _SAFE_CODE.fullmatch(code):
            return code
    return type(exc).__name__


def _page_token(body: Any) -> str:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or "next_page_token" not in data:
        return "absent"
    token = data["next_page_token"]
    if not isinstance(token, str):
        return f"present, not a string ({_kind(token)})"
    return "present (value not shown)" if token else "empty (last page)"


def report_lines(plan: _Plan, capture: _ShapeCapture, outcome: str) -> tuple[list[str], int]:
    """The printed report for one captured read, and the exit code."""
    lines = [
        "TikTok response shape (structure only: no values are shown)",
        f"request: {plan.describe}",
        f"http status: {capture.status}",
    ]
    if capture.too_large:
        lines += [f"body: larger than {_MAX_BODY} bytes, not examined", f"our parser: {outcome}"]
        return lines, 1
    if capture.body is None:
        lines += ["body: not read", f"our parser: {outcome}"]
        return lines, 1
    try:
        body = json.loads(capture.body)
    except ValueError:
        lines += [f"body: not JSON ({len(capture.body)} bytes)", f"our parser: {outcome}"]
        return lines, 1
    code = body.get("code") if isinstance(body, dict) else None
    shown = code if type(code) is int else f"<{_kind(code)}>"
    lines.append(f"provider code: {shown}  (provider message is never shown)")
    if plan.mode in ("orders", "products"):
        lines.append(f"page token: {_page_token(body)}")
    lines.append(f"our parser: {outcome}")
    seen = observe(body)
    lines += ["", "STRUCTURE", *("  " + line for line in structure_lines(seen))]
    lines += [
        "",
        "OUR MODEL SAYS",
        *("  " + line for line in verdict_lines(compare_shape(seen, expected_paths(plan.mode)))),
    ]
    return lines, 0 if code == 0 and type(code) is int else 1


async def run(
    settings: Settings, mode: str, target: str | None, client_id: UUID | None, store_id: UUID | None
) -> int:
    try:
        plan = _plan(mode, target, datetime.now(UTC))
    except ValidationError:
        print(f"refused: that {mode} id is not one the sync would send", file=sys.stderr)
        return 2
    app_pool = db.build_pool(str(settings.DATABASE_URL), min_size=1, max_size=3)
    await app_pool.open(wait=True)
    captures: list[_ShapeCapture] = []

    def wrap(inner: httpx.AsyncBaseTransport) -> httpx.AsyncBaseTransport:
        captures.append(_ShapeCapture(inner))
        return captures[-1]

    try:
        try:
            store = await _choose_store(settings, app_pool, client_id, store_id)
        except _Refused as refused:
            print(f"refused: {refused.text}", file=sys.stderr)
            return 2
        assert store.grant_id is not None
        try:
            await processor.fenced_read(
                app_pool,
                store.client_id,
                store.grant_id,
                plan.request,
                load_app=build_app_loader(settings, app_pool),
                decrypt=decrypt,
                wrap_transport=wrap,
            )
            outcome = "ok"
        except Exception as exc:  # the shape is printed whatever our parser thought of it
            outcome = _safe_code(exc)
    finally:
        await app_pool.close()
    if not captures or captures[0].status is None:
        print(f"failed: {outcome} (no response to examine)", file=sys.stderr)
        return 1
    lines, code = report_lines(plan, captures[0], outcome)
    print("\n".join(redact_line(line) for line in lines))
    return code


def main(argv: list[str]) -> int:
    # Nothing this run touches may log; restore the previous level for an embedding process.
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return _main(argv)
    finally:
        logging.disable(previous)


def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="scripts.tiktok_probe", description=__doc__)
    parser.add_argument("mode", choices=["orders", "products", "order", "product"])
    parser.add_argument("target", nargs="?", help="the order or product id (order, product)")
    parser.add_argument("--client-id", type=UUID)
    parser.add_argument("--store-id", type=UUID)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    if (args.mode in ("order", "product")) != (args.target is not None):
        print(
            "refused: order and product take one id; orders and products take none", file=sys.stderr
        )
        return 2
    if args.store_id is not None and args.client_id is None:
        print("refused: --store-id needs --client-id", file=sys.stderr)
        return 2
    try:
        settings = get_settings()
    except Exception as exc:
        print(f"failed: settings did not load ({type(exc).__name__})", file=sys.stderr)
        return 1
    if not settings.tiktok_enabled:
        print(
            "refused: TikTok is off here (app key, secret or service id not set)", file=sys.stderr
        )
        return 2
    try:
        return asyncio.run(run(settings, args.mode, args.target, args.client_id, args.store_id))
    except Exception as exc:
        print(f"failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
