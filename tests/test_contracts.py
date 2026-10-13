"""LG02 typed contracts, examples, and schema stability."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from csa.contracts.agent_output import AgentWireResponse
from csa.contracts.envelope import (
    Attachment,
    Channel,
    Contact,
    ConversationRef,
    Envelope,
    Message,
    Meta,
)
from csa.contracts.facts import AgentError, AgentResult, AgentResultData, Fact, ResultMeta
from csa.contracts.plan import Intent, Plan
from csa.contracts.state import Context, TurnState
from pydantic import ValidationError

TRACE_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
CLIENT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def envelope_example() -> Envelope:
    return Envelope(
        trace_id=TRACE_ID,
        received_at=datetime(2026, 10, 11, tzinfo=UTC),
        channel=Channel.TELEGRAM,
        idempotency_key="telegram:endpointpublicid_A01:evt-1",
        client_id=CLIENT_ID,
        contact=Contact(
            id=None,
            external_id="tg-100",
            channel=Channel.TELEGRAM,
            display_name=None,
        ),
        conversation=ConversationRef(
            id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
            channel_thread="chat-100",
            turn_count=1,
            status="open",
        ),
        message=Message(
            body_raw="Where is order ORD-A-1001?",
            body_redacted="Where is order ORD-A-1001?",
            language="en",
            attachments=[Attachment(kind="image", mime_type="image/png")],
        ),
        meta=Meta(),
    )


def test_envelope_json_round_trip_preserves_channel_and_ids() -> None:
    original = envelope_example()
    restored = Envelope.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.channel is Channel.TELEGRAM
    assert restored.trace_id == TRACE_ID


def test_envelope_does_not_accept_unknown_fields() -> None:
    payload = envelope_example().model_dump(mode="json")
    payload["unreviewed_field"] = "not part of the contract"

    with pytest.raises(ValidationError):
        Envelope.model_validate(payload)


def test_plan_limits_parallel_tasks_and_validates_confidence() -> None:
    base = {
        "reply_strategy": "auto_reply",
        "intent": Intent.ORDER,
        "confidence": 0.8,
        "source": "fast_path",
    }
    plan = Plan.model_validate(base)
    assert plan.tasks == []
    assert Plan.model_validate(
        {
            **base,
            "tasks": [
                {"task_id": f"t-{i}", "agent": "order_agent", "input": {"action": "status"}}
                for i in range(3)
            ],
        }
    )
    with pytest.raises(ValidationError):
        Plan.model_validate({**base, "confidence": 1.1})
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                **base,
                "tasks": [{"task_id": str(i), "agent": "x", "input": {}} for i in range(4)],
            }
        )


def test_turn_state_uses_small_typed_context() -> None:
    state = TurnState(
        envelope=envelope_example(),
        ctx=Context(summary="Customer asked about an order.", recent_turn_ids=[TRACE_ID]),
    )

    assert state.envelope.trace_id == TRACE_ID
    assert state.ctx is not None
    assert state.ctx.summary == "Customer asked about an order."
    assert state.results == []


def test_agent_result_round_trips_n8n_style_facts_and_ui() -> None:
    result = AgentResult(
        ok=True,
        status="ok",
        task_id="order-status-1",
        agent="order_agent",
        data=AgentResultData(
            facts=[
                Fact(
                    id="order-status",
                    text="ORD-A-1001 is shipped",
                    source="orders.status",
                )
            ]
        ),
        meta=ResultMeta(trace_id=TRACE_ID, latency_ms=23, model=None, tokens=0),
    )

    decoded = AgentResult.model_validate_json(result.model_dump_json())
    assert decoded == result
    assert decoded.facts[0].kind == "verbatim"
    assert decoded.facts[0].text == "ORD-A-1001 is shipped"


@pytest.mark.parametrize(
    ("action", "facts", "ui_options"),
    [
        ("list", ["You have two active orders"], 2),
        ("status", ["ORD-A-1001 is shipped"], 0),
        ("search", ["Found product SKU-100"], 1),
        ("draft", ["Draft ORD-A-1002 created"], 0),
        ("confirm", ["ORD-A-1002 is confirmed"], 0),
        ("cancel", ["ORD-A-1002 is cancelled"], 0),
    ],
)
def test_order_agent_result_examples_validate(
    action: str, facts: list[str], ui_options: int
) -> None:
    payload = {
        "ok": True,
        "status": "ok",
        "task_id": f"order-{action}-1",
        "agent": "order_agent",
        "data": {
            "facts": [
                {
                    "id": f"{action}-fact-{index}",
                    "text": text,
                    "source": f"orders.{action}",
                }
                for index, text in enumerate(facts)
            ],
            "ui": {
                "options": [
                    {"label": f"Option {index}", "action_id": f"a:{index}"}
                    for index in range(ui_options)
                ]
            },
            "suggested_actions": [],
        },
        "meta": {
            "trace_id": str(TRACE_ID),
            "latency_ms": 5,
            "model": None,
            "tokens": 0,
        },
    }

    result = AgentResult.model_validate(payload)
    assert len(result.facts) == len(facts)
    assert len(result.ui.options) == ui_options
    assert result.model_dump(mode="json")["data"]["facts"][0]["text"] == facts[0]


def test_actual_nested_order_list_output_round_trips_unchanged() -> None:
    payload = {
        "ok": True,
        "status": "ok",
        "task_id": "task1",
        "agent": "order_agent",
        "data": {
            "facts": [
                {
                    "source": "orders.ORD-TEST-001",
                    "kind": "verbatim",
                    "as_of": "2026-10-08T09:28:04.323Z",
                    "confidence": 1,
                    "id": "f1",
                    "text": (
                        "Order ORD-TEST-001 is confirmed. Total: MYR 59.90. Contains items: "
                        "Test Gadget (SKU: TEST-001, Quantity: 1)."
                    ),
                }
            ],
            "suggested_actions": [],
        },
        "meta": {
            "trace_id": "5f8dc3e9-98c7-48fa-8b77-b20f80835e4d",
            "latency_ms": 180,
        },
    }
    result = AgentResult.model_validate(payload)

    assert result.facts[0].id == "f1"
    assert result.data.ui.options == []
    serialized = result.model_dump(mode="json", exclude_unset=True)
    assert set(serialized) == set(payload)
    assert set(serialized["data"]) == set(payload["data"])
    assert serialized["data"]["facts"][0]["source"] == "orders.ORD-TEST-001"
    assert AgentResult.model_validate(serialized) == result


def test_actual_nested_order_status_output_validates() -> None:
    payload = {
        "ok": True,
        "status": "ok",
        "task_id": "task-status-test",
        "agent": "order_agent",
        "data": {
            "facts": [
                {
                    "source": "orders.ORD-TEST-002",
                    "kind": "verbatim",
                    "as_of": "2026-10-10T20:29:56.133Z",
                    "confidence": 1,
                    "id": "f1",
                    "text": (
                        "Order ORD-TEST-002 is confirmed. Placed on 05 Oct 2026. Total: MYR 59.90."
                    ),
                },
                {
                    "source": "orders.ORD-TEST-002",
                    "kind": "verbatim",
                    "as_of": "2026-10-10T20:29:56.133Z",
                    "confidence": 1,
                    "id": "f2",
                    "text": "This order has not shipped yet, so there is no tracking number.",
                },
                {
                    "source": "orders.ORD-TEST-002",
                    "kind": "verbatim",
                    "as_of": "2026-10-10T20:29:56.133Z",
                    "confidence": 1,
                    "id": "f3",
                    "text": (
                        "The order contains the following items: Test Gadget "
                        "(SKU: TEST-002, Quantity: 1)."
                    ),
                },
            ],
            "suggested_actions": [],
        },
        "meta": {
            "trace_id": str(TRACE_ID),
            "latency_ms": 85,
        },
    }

    result = AgentResult.model_validate(payload)

    assert result.status == "ok"
    assert [fact.id for fact in result.facts] == ["f1", "f2", "f3"]
    assert result.facts[0].confidence == 1
    assert result.facts[1].text.endswith("no tracking number.")
    assert result.data.suggested_actions == []
    assert result.meta.latency_ms == 85


def test_actual_nested_order_search_output_preserves_legacy_callback_data() -> None:
    payload = {
        "ok": True,
        "status": "ok",
        "task_id": "task-search-test",
        "agent": "order_agent",
        "data": {
            "facts": [
                {
                    "source": f"products.{sku}",
                    "kind": "verbatim",
                    "as_of": "2026-10-05T15:33:25.423Z",
                    "confidence": 1,
                    "id": f"f{index}",
                    "text": f"Test product {index} ({sku}) costs MYR 59.90 per unit.",
                }
                for index, sku in enumerate(
                    [
                        "TEST-GADGET-002",
                        "TEST-GADGET-004",
                        "TEST-GADGET-003",
                        "TEST-CASE-011",
                        "TEST-BUNDLE-020",
                    ],
                    start=1,
                )
            ],
            "suggested_actions": ["offer_order"],
            "ui": {
                "options": [
                    {
                        "label": f"Test product {index} — MYR 59.90",
                        "callback_data": f"ord:pick:TEST-SKU-{index:03}",
                    }
                    for index in range(1, 6)
                ]
            },
        },
        "meta": {
            "trace_id": str(TRACE_ID),
            "latency_ms": 122,
        },
    }

    result = AgentResult.model_validate(payload)

    assert len(result.facts) == 5
    assert result.suggested_actions == ["offer_order"]
    assert result.ui.options[0].action_id is None
    assert result.ui.options[0].legacy_callback_data == "ord:pick:TEST-SKU-001"
    assert "callback_data" not in result.model_dump(mode="json")["data"]["ui"]["options"][0]


def test_actual_nested_order_draft_output_validates_confirmation_and_metadata() -> None:
    payload = {
        "ok": True,
        "status": "ok",
        "task_id": "task-draft-test",
        "agent": "order_agent",
        "data": {
            "facts": [
                {
                    "source": "orders.ORD-TEST-74012",
                    "kind": "verbatim",
                    "as_of": "2026-10-05T15:34:01.627Z",
                    "confidence": 1,
                    "id": "f1",
                    "text": (
                        "Draft order ORD-TEST-74012 contains Test Gadget "
                        "(SKU: TEST-GADGET-002, Quantity: 5) at MYR 59.90 each. "
                        "Order total: MYR 299.50."
                    ),
                },
                {
                    "source": "orders.ORD-TEST-74012",
                    "kind": "verbatim",
                    "as_of": "2026-10-05T15:34:01.627Z",
                    "confidence": 1,
                    "id": "f2",
                    "text": (
                        "This order is not confirmed yet. It needs confirmation to be "
                        "placed, and payment is collected on delivery."
                    ),
                },
            ],
            "suggested_actions": ["await_confirmation"],
            "ui": {
                "confirmation": {
                    "required": True,
                    "order_id": "ORD-TEST-74012",
                    "expires_at": "2026-10-05T16:04:01.579Z",
                    "accept": {
                        "label": "Confirm order",
                        "callback_data": "ord:confirm:ORD-TEST-74012:cfm_testtoken",
                    },
                    "reject": {
                        "label": "Cancel",
                        "callback_data": "ord:cancel:ORD-TEST-74012:cfm_testtoken",
                    },
                }
            },
        },
        "meta": {
            "was_duplicate": False,
            "trace_id": str(TRACE_ID),
            "latency_ms": 175,
        },
    }

    result = AgentResult.model_validate(payload)

    assert result.data.ui.confirmation is not None
    assert result.data.ui.confirmation.required is True
    assert result.data.ui.confirmation.accept.action_id is None
    assert (
        result.data.ui.confirmation.accept.legacy_callback_data
        == "ord:confirm:ORD-TEST-74012:cfm_testtoken"
    )
    assert result.meta.was_duplicate is False


def test_actual_nested_order_confirm_output_separates_private_approval_data() -> None:
    payload = {
        "ok": True,
        "status": "ok",
        "task_id": "task-confirm-test",
        "agent": "order_agent",
        "data": {
            "facts": [
                {
                    "source": "orders.ORD-TEST-74012",
                    "kind": "verbatim",
                    "as_of": "2026-10-05T15:34:07.550Z",
                    "confidence": 1,
                    "id": "f1",
                    "text": (
                        "Order ORD-TEST-74012 has been submitted for approval. "
                        "Order total: MYR 299.50."
                    ),
                },
                {
                    "source": "orders.ORD-TEST-74012",
                    "kind": "verbatim",
                    "as_of": "2026-10-05T15:34:07.550Z",
                    "confidence": 1,
                    "id": "f2",
                    "text": (
                        "The order is not confirmed until our team approves it. Payment is "
                        "collected on delivery and no payment has been taken."
                    ),
                },
            ],
            "suggested_actions": [],
            "ui": {"confirmation": None, "options": []},
        },
        "_approval": {
            "order_id": "ORD-TEST-74012",
            "total": "299.50",
            "currency": "MYR",
            "lines": [
                {
                    "qty": 5,
                    "sku": "TEST-GADGET-002",
                    "name": "Test Gadget",
                    "unit_price": 59.9,
                }
            ],
            "customer_name": "Test Customer",
        },
        "meta": {
            "trace_id": str(TRACE_ID),
            "latency_ms": 963,
        },
    }

    response = AgentWireResponse.model_validate(payload)

    assert response.result.data.ui.confirmation is None
    assert response.approval is not None
    assert response.approval.order_id == "ORD-TEST-74012"
    assert response.approval.total == Decimal("299.50")
    assert response.approval.lines[0].qty == 5
    assert response.approval.customer_name == "Test Customer"
    assert "_approval" not in response.model_dump(mode="json")
    assert "Test Customer" not in repr(response)


def test_actual_nested_order_cancel_output_validates() -> None:
    payload = {
        "ok": True,
        "status": "ok",
        "task_id": "task-cancel-test",
        "agent": "order_agent",
        "data": {
            "facts": [
                {
                    "source": "orders.ORD-TEST-74012",
                    "kind": "verbatim",
                    "as_of": "2026-10-05T15:34:17.438Z",
                    "confidence": 1,
                    "id": "f1",
                    "text": "Order ORD-TEST-74012 has been cancelled. Nothing was charged.",
                }
            ],
            "suggested_actions": [],
            "ui": {"confirmation": None, "options": []},
        },
        "meta": {
            "trace_id": str(TRACE_ID),
            "latency_ms": 121,
        },
    }

    result = AgentResult.model_validate(payload)

    assert result.status == "ok"
    assert result.facts[0].text.endswith("Nothing was charged.")
    assert result.ui.confirmation is None
    assert result.ui.options == []


def test_agent_error_categories_are_closed() -> None:
    error = AgentError(code="not_found", category="not_found", message="Not found")
    assert error.retryable is False
    with pytest.raises(ValidationError):
        AgentError.model_validate({"code": "bad", "category": "mystery", "message": "Bad"})


def test_agent_result_schema_matches_snapshot() -> None:
    snapshot_path = Path(__file__).parent / "snapshots" / "agent_result.schema.json"
    expected = json.loads(snapshot_path.read_text(encoding="utf-8"))

    assert AgentResult.model_json_schema() == expected
