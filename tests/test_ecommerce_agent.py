"""The customer-facing E-commerce spoke is read-only (spec 6.7): pure tests with a fake reader.

The database-backed reads (order lookup, limit, live stock) are in tests/test_marketplace_reads.py.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from csa.agents.base import AgentContext
from csa.agents.ecommerce import (
    ACTION_RISK,
    EcommerceAgent,
    EcommerceInput,
    TikTokShopAdapter,
    TikTokShopCredentials,
)
from csa.agents.ecommerce.agent import (
    ORDER_LIMIT_TEXT,
    ORDER_NOT_FOUND_TEXT,
    ORDER_NUMBER_UNREADABLE_HINT,
    STOCK_NOT_FOUND_FACT,
    STOCK_NOT_FOUND_TEXT,
    STOCK_UNCHECKED_TEXT,
    tiktok_stock_fact,
)
from csa.agents.ecommerce.models import (
    ListingChoice,
    LiveStock,
    OrderLookup,
    OrderStatusFact,
    SkuStock,
    StockLookup,
)
from csa.agents.orders.availability import (
    SHOP_NOT_FOUND_FACT,
    SHOP_NOT_FOUND_TEXT,
    availability_facts,
    shop_fact,
)
from csa.agents.runner import SpokeRunner
from csa.contracts.callbacks import PendingActionStore
from csa.contracts.envelope import Envelope
from csa.contracts.plan import Task
from csa.contracts.registry import CapabilityRegistry
from csa.core.pii import redact
from csa.turn.deps import _resolve_order_number  # pyright: ignore[reportPrivateUsage]
from pydantic import ValidationError

from tests.test_coordinator import make_envelope

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)
LISTING = UUID("00000000-0000-4000-8000-000000000019")
LUHN_ORDER = "576461413038785750"  # passes the card check, so redact() masks it
COPY_HEAD = "Synthetic mug (Black / M), MYR 19.90 on our TikTok Shop: "


def _sample_creds() -> TikTokShopCredentials:
    return TikTokShopCredentials(
        app_key="test_app_key",
        app_secret="test_app_secret_123456",
        access_token="test_access_token_xyz",
        shop_cipher="test_shop_cipher_my",
    )


def test_tiktok_shop_signing_and_webhook_verification() -> None:
    """Verifies HMAC-SHA256 signature calculation and webhook verification."""
    creds = _sample_creds()
    adapter = TikTokShopAdapter(creds)

    path = "/order/202309/orders/search"
    query_params = {
        "timestamp": "1700000000",
        "app_key": creds.app_key,
        "shop_cipher": creds.shop_cipher,
        "sign": "ignore_me",
        "access_token": "ignore_me_too",
    }
    body = '{"page_size":10}'
    sign1 = adapter.sign_request(path, query_params, body)
    assert isinstance(sign1, str)
    assert len(sign1) == 64
    assert sign1 == adapter.sign_request(path, query_params, body)
    assert sign1 != adapter.sign_request(path, query_params, '{"page_size":20}')

    import hashlib
    import hmac

    raw_payload = b'{"event":"ORDER_STATUS_CHANGE","order_id":"12345"}'
    expected_sig = hmac.new(
        creds.app_secret.encode("utf-8"), raw_payload, hashlib.sha256
    ).hexdigest()
    assert adapter.verify_webhook_signature(raw_payload, expected_sig, creds.app_secret) is True
    assert (
        adapter.verify_webhook_signature(raw_payload, "invalid_sig_hex", creds.app_secret) is False
    )


# -- the input surface ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "fulfill_order", "order_id": "TK1001", "tracking_number": "TRACK-999"},
        {"action": "sync_inventory", "inventory_items": [{"sku": "SKU-1", "available_qty": 1}]},
        {"action": "cancel_order", "order_id": "TK1001"},
        {"action": "approve_refund", "order_id": "TK1001"},
        {"action": "cancel", "order_number": "1234567890123456"},
        {"action": "refund", "order_number": "1234567890123456"},
        {"action": "get_order", "order_id": "TK1001"},
        {"action": "search_orders"},
        {"action": "get_product", "sku": "SKU-1"},
        # The Shopee and Lazada reach is gone too: there is no platform field to set.
        {"action": "order_status", "order_number": "1234567890", "platform": "shopee"},
        {"action": "stock_check", "query": "mug", "platform": "lazada"},
        {"action": "order_status", "order_number": "1234567890", "platform": "tiktok_shop"},
    ],
)
def test_input_model_has_no_write_actions(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EcommerceInput.model_validate(payload)


def test_input_schema_offers_exactly_the_one_read_action() -> None:
    schema = EcommerceInput.model_json_schema()
    assert schema["properties"]["action"]["const"] == "order_status"
    assert set(schema["properties"]) == {"action", "order_number"}
    assert ACTION_RISK == {"order_status": "read"}
    assert EcommerceAgent.ACTION_RISK == ACTION_RISK


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "order_status"},
        {"action": "order_status", "order_number": ""},
        {"action": "order_status", "order_number": "57 64 61"},
        {"action": "order_status", "order_number": "5764'; DROP"},
        {"action": "order_status", "order_number": "5" * 101},
        {"action": "order_status", "order_number": "576461413038785752", "query": "mug"},
        {"action": "order_status", "order_number": "[EMAIL_0]"},
        {"action": "order_status", "order_number": "[CARD_x]"},
        {"action": "order_status", "order_number": "[CARD_0] 1"},
        {"action": "order_status", "order_number": "[CARD_1234]"},
    ],
)
def test_input_needs_exactly_the_field_of_its_action(payload: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EcommerceInput.model_validate(payload)


def test_input_accepts_order_numbers() -> None:
    assert EcommerceInput(action="order_status", order_number="576461413038785752")
    assert EcommerceInput(action="order_status", order_number="TT-12_ab").order_number == (
        "TT-12_ab"
    )
    # The planner only sees redacted text, so a placeholder is accepted for order_status.
    for placeholder in ("[CARD_0]", "[PHONE_12]"):
        assert EcommerceInput(action="order_status", order_number=placeholder)


def test_agent_cannot_be_built_without_a_reader() -> None:
    with pytest.raises(TypeError):
        EcommerceAgent()  # type: ignore[call-arg]
    agent = EcommerceAgent(FakeReader())
    assert agent.name == "ecommerce_agent"
    assert agent.side_effects == "none"
    assert agent.max_retries == 0
    order = EcommerceInput(action="order_status", order_number="576461413038785752")
    assert agent.risk(order) == "read"


# -- wording --------------------------------------------------------------------------------


class FakeReader:
    def __init__(self, order: OrderLookup | None = None, stock: StockLookup | None = None) -> None:
        self.order = order or OrderLookup(outcome="not_found")
        self.stock = stock or StockLookup(outcome="not_found")
        self.calls: list[tuple[str, str]] = []

    async def order_status(self, ctx: AgentContext, order_number: str) -> OrderLookup:
        self.calls.append(("order_status", order_number))
        return self.order

    async def stock_check(self, ctx: AgentContext, query: str) -> StockLookup:
        self.calls.append(("stock_check", query))
        return self.stock


def ctx(now: datetime = NOW) -> AgentContext:
    return AgentContext(
        client_id=uuid4(),
        contact_id=uuid4(),
        trace_id=uuid4(),
        conversation_id=uuid4(),
        idempotency_key="idem-ecom",
        channel="telegram",
        pool=MagicMock(),
        pending_actions=MagicMock(spec=PendingActionStore),
        now=lambda: now,
    )


async def run(reader: FakeReader, **inp: Any):  # type: ignore[no-untyped-def]
    return await EcommerceAgent(reader).run(ctx(), EcommerceInput.model_validate(inp))


async def test_order_status_found_names_only_status_tracking_and_day() -> None:
    fact = OrderStatusFact(
        status_text="shipped",
        tracking_no="SYNTH-TRACK-1",
        carrier="Synthetic Courier",
        shipped_or_delivered_on=date(2026, 11, 3),
    )
    reader = FakeReader(order=OrderLookup(outcome="found", fact=fact))
    result = await run(reader, action="order_status", order_number="576461413038785752")
    assert result.ok
    (only,) = result.facts
    assert only.id == "tiktok_order_status"
    assert only.text == (
        "Your TikTok order is shipped. Last update: 03 Nov 2026. "
        "Tracking number: SYNTH-TRACK-1 (Synthetic Courier)."
    )
    # The fact id never carries the number (agent_runs records fact ids).
    assert "576461413038785752" not in result.model_dump_json()
    assert reader.calls == [("order_status", "576461413038785752")]


async def test_order_status_without_tracking_or_day() -> None:
    fact = OrderStatusFact(status_text="being prepared")
    result = await run(
        FakeReader(order=OrderLookup(outcome="found", fact=fact)),
        action="order_status",
        order_number="1",
    )
    assert result.facts[0].text == "Your TikTok order is being prepared."


async def test_unknown_order_number_reply() -> None:
    result = await run(FakeReader(), action="order_status", order_number="999")
    assert [(f.id, f.text) for f in result.facts] == [
        ("tiktok_order_not_found", ORDER_NOT_FOUND_TEXT)
    ]
    assert ORDER_NOT_FOUND_TEXT == (
        "I couldn't find that TikTok order number. Please check it, or ask our staff."
    )


async def test_limited_lookup_reply() -> None:
    result = await run(
        FakeReader(order=OrderLookup(outcome="limited")), action="order_status", order_number="9"
    )
    assert [(f.id, f.text) for f in result.facts] == [
        ("tiktok_order_lookup_limit", "Please ask our staff for more help.")
    ]
    assert ORDER_LIMIT_TEXT == "Please ask our staff for more help."


def stock(
    qty: int | None, *, live: bool, as_of: datetime = NOW, sellable: bool = True
) -> StockLookup:
    return StockLookup(
        outcome="match",
        stock=LiveStock(
            listing_id=LISTING,
            product_name="Synthetic mug",
            skus=(
                SkuStock(
                    listing_id=LISTING,
                    external_sku_id="SYNTH-SKU-1",
                    variant_name="Black / M",
                    seller_sku="MUG-BLK-M",
                    stock_qty=qty,
                    price=Decimal("19.90"),
                    currency="MYR",
                ),
                SkuStock(
                    listing_id=uuid4(),
                    external_sku_id="SYNTH-SKU-2",
                    variant_name="White / L",
                    seller_sku=None,
                    stock_qty=99,
                    price=Decimal("1.00"),
                    currency="MYR",
                ),
            ),
            checked_live=live,
            as_of=as_of,
            sellable=sellable,
        ),
    )


def test_live_stock_reply_has_count_price_and_shop() -> None:
    only = tiktok_stock_fact(stock(8, live=True), NOW)
    assert only.id == "tiktok_stock"
    assert only.text == "Synthetic mug (Black / M), MYR 19.90: 8 available on our TikTok Shop."
    assert only.as_of == NOW


@pytest.mark.parametrize(
    ("age", "text"),
    [
        (
            timedelta(hours=2, minutes=5),
            f"{COPY_HEAD}8 were available as of 2 hours ago.",
        ),
        (
            timedelta(minutes=40),
            f"{COPY_HEAD}8 were available as of 40 minutes ago.",
        ),
        (
            timedelta(hours=5, minutes=59),
            f"{COPY_HEAD}8 were available as of 5 hours ago.",
        ),
        (
            timedelta(hours=6),
            f"Synthetic mug (Black / M) on our TikTok Shop: {STOCK_UNCHECKED_TEXT}",
        ),
        (
            timedelta(days=3),
            f"Synthetic mug (Black / M) on our TikTok Shop: {STOCK_UNCHECKED_TEXT}",
        ),
    ],
)
def test_copy_reply_by_age_when_the_live_check_failed(age: timedelta, text: str) -> None:
    fact = tiktok_stock_fact(stock(8, live=False, as_of=NOW - age), NOW)
    assert fact.text == text
    assert STOCK_UNCHECKED_TEXT == "I can't check right now. Our staff will confirm."


def test_choices_reply_lists_the_matches() -> None:
    choices = tuple(
        ListingChoice(
            listing_id=uuid4(),
            product_name="Synthetic mug",
            variant_name=f"Colour {index}",
            seller_sku=f"MUG-{index}" if index % 2 else None,
        )
        for index in range(1, 6)
    )
    only = tiktok_stock_fact(StockLookup(outcome="choices", choices=choices), NOW)
    assert only.id == "tiktok_stock_choices"
    assert only.text.startswith("Several items on our TikTok Shop match. Which one do you mean?")
    assert "1. Synthetic mug (Colour 1) (SKU MUG-1); 2. Synthetic mug (Colour 2); " in only.text
    assert only.text.endswith("5. Synthetic mug (Colour 5) (SKU MUG-5)")


def test_not_found_stock_reply_offers_staff() -> None:
    fact = tiktok_stock_fact(StockLookup(outcome="not_found"), NOW)
    assert fact.id == "tiktok_stock_not_found"
    assert fact.text == STOCK_NOT_FOUND_TEXT and "staff" in fact.text


def test_a_sku_removed_since_the_match_is_not_found() -> None:
    lookup = stock(8, live=True)
    assert lookup.stock is not None
    gone = lookup.model_copy(
        update={"stock": lookup.stock.model_copy(update={"skus": lookup.stock.skus[1:]})}
    )
    assert tiktok_stock_fact(gone, NOW).id == "tiktok_stock_not_found"


# -- the capability gate --------------------------------------------------------------------


async def test_agent_needs_the_marketplaces_package() -> None:
    """Without the ecommerce_agent capability (the marketplaces package) nothing is read."""
    reader = FakeReader(order=OrderLookup(outcome="found", fact=OrderStatusFact(status_text="x")))
    registry = AsyncMock(spec=CapabilityRegistry)
    registry.allows_execution.return_value = False
    runner = SpokeRunner({"ecommerce_agent": EcommerceAgent(reader)}, capabilities=registry)
    inputs: list[dict[str, object]] = [
        {"action": "order_status", "order_number": "576461413038785752"},
    ]
    for inp in inputs:
        result = await runner.execute(Task(task_id="t", agent="ecommerce_agent", input=inp), ctx())
        assert result.error is not None and result.error.code == "capability_unavailable"
        assert result.facts == []
    assert reader.calls == []
    assert {call.args[1] for call in registry.allows_execution.await_args_list} == {
        "ecommerce_agent"
    }


async def test_runner_risk_reads_the_new_surface() -> None:
    runner = SpokeRunner({"ecommerce_agent": EcommerceAgent(FakeReader())})

    def risk(**inp: object):  # type: ignore[no-untyped-def]
        return runner.risk(Task(task_id="t", agent="ecommerce_agent", input=dict(inp)))

    assert risk(action="order_status", order_number="1") == "read"
    assert risk(action="stock_check", query="mug") is None
    assert risk(action="fulfill_order", order_id="1", tracking_number="T") is None
    assert risk(action="sync_inventory", sku="A") is None


async def test_an_unresolved_placeholder_is_never_looked_up() -> None:
    reader = FakeReader(order=OrderLookup(outcome="found", fact=OrderStatusFact(status_text="x")))
    result = await run(reader, action="order_status", order_number="[CARD_0]")
    assert not result.ok and result.error is not None
    assert result.error.code == "order_number_unreadable"
    assert result.error.category == "validation"
    assert result.error.customer_safe_hint == ORDER_NUMBER_UNREADABLE_HINT
    assert reader.calls == []


def test_a_listing_that_is_not_selling_is_never_available() -> None:
    for live in (True, False):
        fact = tiktok_stock_fact(stock(8, live=live, sellable=False), NOW)
        assert fact.id == STOCK_NOT_FOUND_FACT
        assert "available" not in fact.text


def test_a_copy_under_a_minute_old_says_less_than_a_minute() -> None:
    fact = tiktok_stock_fact(stock(8, live=False, as_of=NOW - timedelta(seconds=20)), NOW)
    assert fact.text == f"{COPY_HEAD}8 were available as of less than a minute ago."


# -- the merge of our shop and TikTok Shop (no database) -------------------------------------


def _row(sku: str = "LAMP-1", qty: int = 7) -> dict[str, Any]:
    return {
        "sku": sku,
        "name": "Synthetic lamp",
        "price": Decimal("12"),
        "currency": "MYR",
        "available_qty": qty,
    }


def test_a_shop_fact_names_the_shop_and_says_out_of_stock_at_zero() -> None:
    fact = shop_fact(2, _row())
    assert (fact.id, fact.source) == ("shop_product_2", "order_agent")
    assert fact.text == "Synthetic lamp (LAMP-1): MYR 12.00, 7 available in our shop."
    assert shop_fact(0, _row(qty=0)).text == (
        "Synthetic lamp (LAMP-1): MYR 12.00, out of stock in our shop."
    )


def test_the_merge_gives_every_shop_row_then_the_tiktok_match() -> None:
    rows = [_row("LAMP-1"), _row("LAMP-2", 0)]
    facts = availability_facts(rows, stock(8, live=True), NOW)
    assert [f.id for f in facts] == ["shop_product_0", "shop_product_1", "tiktok_stock"]
    assert facts[2].text.endswith("8 available on our TikTok Shop.")
    # Our shop alone when TikTok was not checked, or found nothing.
    assert [f.id for f in availability_facts(rows, None, NOW)] == [
        "shop_product_0",
        "shop_product_1",
    ]
    assert [f.id for f in availability_facts(rows, StockLookup(outcome="not_found"), NOW)] == [
        "shop_product_0",
        "shop_product_1",
    ]


def test_tiktok_choices_and_unsellable_matches_add_nothing_beside_a_shop_row() -> None:
    choices = tuple(
        ListingChoice(
            listing_id=uuid4(), product_name="Synthetic mug", variant_name=name, seller_sku=None
        )
        for name in ("A", "B")
    )
    lookup = StockLookup(outcome="choices", choices=choices)
    assert [f.id for f in availability_facts([_row()], lookup, NOW)] == ["shop_product_0"]
    assert [f.id for f in availability_facts([], lookup, NOW)] == ["tiktok_stock_choices"]
    unsellable = stock(8, live=True, sellable=False)
    assert [f.id for f in availability_facts([_row()], unsellable, NOW)] == ["shop_product_0"]


def test_nothing_found_gives_one_not_found_fact_by_what_was_checked() -> None:
    (shop_only,) = availability_facts([], None, NOW)
    assert (shop_only.id, shop_only.text) == (SHOP_NOT_FOUND_FACT, SHOP_NOT_FOUND_TEXT)
    assert SHOP_NOT_FOUND_TEXT == "I couldn't find that item in our shop."
    for lookup in (StockLookup(outcome="not_found"), stock(8, live=True, sellable=False)):
        (both,) = availability_facts([], lookup, NOW)
        assert (both.id, both.text) == (STOCK_NOT_FOUND_FACT, STOCK_NOT_FOUND_TEXT)


def _envelope(raw: str, redacted: str | None = None) -> Envelope:
    envelope = make_envelope(raw)
    message = envelope.message.model_copy(
        update={"body_raw": raw, "body_redacted": redact(raw) if redacted is None else redacted}
    )
    return envelope.model_copy(update={"message": message})


def _lookup(number: str, agent: str = "ecommerce_agent", action: str = "order_status") -> Task:
    return Task(task_id="t0", agent=agent, input={"action": action, "order_number": number})


def test_a_card_like_order_number_is_resolved_from_the_raw_message_only() -> None:
    raw = "Where is my TikTok order 5764 6141 3038 7857 50? Also 600000000000000017"
    envelope = _envelope(raw)
    assert "[CARD_0]" in envelope.message.body_redacted
    assert "[PHONE_0]" in envelope.message.body_redacted
    assert LUHN_ORDER not in envelope.message.body_redacted

    resolved = _resolve_order_number(_lookup("[CARD_0]"), envelope)
    assert resolved.input == {"action": "order_status", "order_number": LUHN_ORDER}
    phone = _resolve_order_number(_lookup("[PHONE_0]"), envelope)
    assert phone.input["order_number"] == "600000000000000017"
    # Unknown placeholders, other actions or agents, plain numbers: untouched.
    unchanged = [
        _lookup("[CARD_1]"),
        _lookup("[CARD_0]", agent="order_agent"),
        _lookup("576461413038785752"),
    ]
    for task in unchanged:
        assert _resolve_order_number(task, envelope) == task
    # A redacted body that is not this raw message's redaction resolves nothing.
    assert (
        _resolve_order_number(_lookup("[CARD_0]"), _envelope(raw, "[CARD_0]")).input["order_number"]
        == "[CARD_0]"
    )
