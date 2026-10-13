"""Normalized agent wire responses with private approval data kept separate."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import Field, model_validator

from csa.contracts.approval import OrderApprovalPayload
from csa.contracts.envelope import ContractModel
from csa.contracts.facts import AgentResult


class AgentWireResponse(ContractModel):
    result: AgentResult
    approval: OrderApprovalPayload | None = Field(default=None, exclude=True, repr=False)

    @model_validator(mode="before")
    @classmethod
    def separate_private_approval(cls, value: Any) -> Any:
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        approval = payload.pop("_approval", None)
        normalized: dict[str, Any] = {"result": payload}
        if approval is not None:
            normalized["approval"] = approval
        return normalized
