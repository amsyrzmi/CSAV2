"""What the customer bot tells a customer about plans and their own subscription (pure)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from csa.agents.staged.subscription_facts import (
    LIVE_STATUSES,
    REQUESTABLE_STATUSES,
    SUBSCRIPTION_SOURCE,
    plan_facts,
    request_fact,
    subscription_facts,
)

ROOT = Path(__file__).resolve().parents[1]
ZONE = "Asia/Kuala_Lumpur"
RENEWS = datetime(2026, 12, 1, 4, 0, tzinfo=UTC)


def row(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": uuid4(),
        "plan": "basic",
        "plan_name": "Basic",
        "billing_interval": "month",
        "status": "active",
        "price": Decimal("10.00"),
        "currency": "MYR",
        "renews_at": RENEWS,
        "cancel_at_period_end": False,
        "resume_at": None,
        "pending_plan": None,
        "pending_plan_name": None,
        "pending_effective_at": None,
    }
    return {**base, **fields}


def texts(rows: list[dict[str, Any]]) -> list[str]:
    return [fact.text for fact in subscription_facts(rows, ZONE)]


def test_the_statuses_are_the_live_and_the_requestable_ones() -> None:
    assert LIVE_STATUSES == ("active", "paused", "pending")
    assert REQUESTABLE_STATUSES == frozenset({"active", "paused"})
    assert SUBSCRIPTION_SOURCE == "subscription_agent"


def test_plan_facts_one_per_plan_with_features_and_interval() -> None:
    facts = plan_facts(
        [
            {
                "code": "basic",
                "name": "Basic",
                "price": Decimal("10"),
                "currency": "MYR",
                "billing_interval": "month",
                "features": ["5 GB data", "Calls"],
            },
            {
                "code": "year",
                "name": "Yearly",
                "price": "99.5",
                "currency": "MYR",
                "billing_interval": "year",
                "features": [],
            },
        ]
    )
    assert [f.id for f in facts] == ["subscription_plan_1", "subscription_plan_2"]
    assert facts[0].text == "Basic: MYR 10.00 a month. Includes: 5 GB data; Calls."
    assert facts[1].text == "Yearly: MYR 99.50 a year."
    assert {f.source for f in facts} == {SUBSCRIPTION_SOURCE}


def test_an_active_plan_renews_and_a_period_end_cancel_ends() -> None:
    assert texts([row()]) == ["Your plan: Basic, MYR 10.00 a month. It renews on 01 Dec 2026."]
    assert texts([row(cancel_at_period_end=True)]) == [
        "Your plan: Basic, MYR 10.00 a month. It ends on 01 Dec 2026 and will not renew."
    ]


def test_paused_with_and_without_a_resume_date() -> None:
    assert texts([row(status="paused")]) == ["Your plan: Basic is paused."]
    assert texts([row(status="paused", resume_at=date(2027, 1, 5))]) == [
        "Your plan: Basic is paused until 05 Jan 2027."
    ]


def test_a_pending_plan_is_waiting_for_payment_with_no_date() -> None:
    assert texts([row(status="pending", renews_at=None)]) == [
        "Your plan: Basic, MYR 10.00 a month, is waiting for payment."
    ]


def test_a_cancelled_plan_says_so_and_an_unknown_status_gives_no_fact() -> None:
    assert texts([row(status="cancelled")]) == ["Your plan: Basic was cancelled."]
    assert texts([row(status="mystery")]) == []


def test_a_scheduled_change_is_added_with_its_date() -> None:
    changing = row(
        pending_plan="pro",
        pending_plan_name="Pro",
        pending_effective_at=datetime(2026, 12, 1, 4, 0, tzinfo=UTC),
    )
    assert texts([changing]) == [
        "Your plan: Basic, MYR 10.00 a month. It renews on 01 Dec 2026. "
        "It changes to Pro on 01 Dec 2026."
    ]
    undated = row(pending_plan="pro", pending_plan_name=None)
    assert texts([undated])[0].endswith(" It is set to change to pro.")


def test_without_a_plan_row_the_interval_is_left_out() -> None:
    orphan = row(plan_name=None, billing_interval=None)
    assert texts([orphan]) == ["Your plan: basic, MYR 10.00. It renews on 01 Dec 2026."]


def test_several_rows_give_one_fact_each_newest_first_as_given() -> None:
    facts = subscription_facts([row(plan_name="Pro"), row(plan_name="Basic")], ZONE)
    assert [f.id for f in facts] == ["subscription_1", "subscription_2"]
    assert facts[0].text.startswith("Your plan: Pro,")


def test_no_rows_is_the_no_subscription_sentence() -> None:
    facts = subscription_facts([], ZONE)
    assert [(f.id, f.text) for f in facts] == [
        ("subscription_none", "I can't find a subscription linked to this chat.")
    ]


def test_request_facts_use_the_exact_copy() -> None:
    def text(*args: Any, **kwargs: Any) -> str:
        return request_fact(*args, **kwargs).text

    assert text("passed", action="request_pause", name="Basic") == (
        "Your request to pause Basic was passed to our staff to decide. Nothing has changed yet."
    )
    assert text("passed", action="request_cancel", name="Basic") == (
        "Your request to cancel Basic was passed to our staff to decide. Nothing has changed yet."
    )
    assert text("passed", action="request_change", name="Basic", new_name="Pro") == (
        "Your request to change Basic to Pro was passed to our staff to decide. "
        "Nothing has changed yet."
    )
    assert text("which", names=["Pro", "Basic"]) == (
        "This chat has more than one plan: Pro, Basic. Which one do you mean?"
    )
    assert text("which", names=["Basic"]) == (
        "I can't find a plan by that name on this chat. "
        "The plan I can see is Basic. Is that the one you mean?"
    )
    assert text("none") == "I can't find a subscription linked to this chat."
    assert text("already_ending", name="Basic", ends=RENEWS) == (
        "Basic is already set to end on 01 Dec 2026 and will not renew."
    )
    assert text("already_paused", name="Basic") == "Basic is already paused."
    assert text("waiting", name="Basic") == (
        "Basic is waiting for payment, so it can't be paused, cancelled or changed yet."
    )
    assert [request_fact(kind).id for kind in ("none", "which")] == [
        "subscription_none",
        "subscription_which",
    ]
    assert request_fact("passed", action="request_pause", name="Basic").id == "subscription_passed"


def test_fact_ids_carry_no_subscription_id() -> None:
    rows = [row(), row(status="paused")]
    ids = [f.id for f in subscription_facts(rows, ZONE)]
    ids += [f.id for f in plan_facts([{"code": "a", "name": "A", "price": 1, "currency": "MYR"}])]
    ids += [request_fact("none").id, request_fact("which", names=["A", "B"]).id]
    for fact_id in ids:
        assert fact_id in {
            "subscription_1",
            "subscription_2",
            "subscription_plan_1",
            "subscription_none",
            "subscription_which",
        }
    for one in rows:
        assert not any(str(one["id"]) in fact_id for fact_id in ids)


def test_the_live_sql_allowlist_names_pending() -> None:
    sql = (ROOT / "db/sql/agents/subscription_list.sql").read_text()
    assert "'active'" in sql and "'paused'" in sql and "'pending'" in sql
    assert "client_id" in sql and "contact_id" in sql
