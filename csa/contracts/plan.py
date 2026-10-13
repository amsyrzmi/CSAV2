"""Coarse intent and coordinator-plan contracts (LG02 §2)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from csa.contracts.envelope import ContractModel


class Intent(StrEnum):
    ORDER = "order"
    REFUND_RETURNS = "refund_returns"
    BILLING_INVOICE = "billing_invoice"
    BOOKING = "booking"
    SUBSCRIPTION = "subscription"
    WARRANTY = "warranty"
    PRODUCT_INFO = "product_info"
    STORE_INFO = "store_info"
    TECHNICAL = "technical"
    COMPLAINT = "complaint"
    HUMAN_REQUEST = "human_request"
    CHITCHAT = "chitchat"
    SPAM = "spam"
    OTHER = "other"


RiskClass = Literal["read", "draft", "commit"]
"""How much one agent action changes the world.

``read`` only looks things up; ``draft`` creates something the customer must still confirm;
``commit`` changes business state. Only ``read`` actions may run inside the agent loop.
"""


class Task(ContractModel):
    task_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    input: dict[str, object]


class Plan(ContractModel):
    tasks: list[Task] = Field(default_factory=list, max_length=3)
    reply_strategy: Literal["auto_reply", "ask_clarifying", "escalate", "ignore", "confirm"]
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    intent: Intent
    sentiment: Literal["positive", "neutral", "frustrated", "angry"] = "neutral"
    confidence: float = Field(ge=0, le=1)
    requires_confirmation: bool = False
    source: Literal["fast_path", "jev", "llm", "policy"]
