"""Agent facts, results, and customer-facing UI contracts (LG02 §1)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from csa.contracts.envelope import ContractModel


class Fact(ContractModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    source: str = Field(min_length=1)
    kind: Literal["verbatim", "derived"] = "verbatim"
    as_of: datetime | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class UiOption(ContractModel):
    label: str = Field(min_length=1)
    action_id: str | None = Field(default=None, min_length=1, max_length=64)
    legacy_callback_data: str | None = Field(default=None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def preserve_legacy_callback_data(cls, value: Any) -> Any:
        if not isinstance(value, Mapping) or "callback_data" not in value:
            return value
        payload = dict(value)
        if "action_id" in payload or "legacy_callback_data" in payload:
            raise ValueError("UI option cannot mix legacy and compact callback identifiers")
        payload["legacy_callback_data"] = payload.pop("callback_data")
        return payload

    @model_validator(mode="after")
    def require_one_callback_identifier(self) -> UiOption:
        if (self.action_id is None) == (self.legacy_callback_data is None):
            raise ValueError("UI option requires exactly one callback identifier")
        return self


class UiConfirmation(ContractModel):
    required: bool
    order_id: str = Field(min_length=1)
    expires_at: datetime
    accept: UiOption
    reject: UiOption


class Ui(ContractModel):
    confirmation: UiConfirmation | None = None
    options: list[UiOption] = Field(default_factory=list, max_length=20)


class AgentError(ContractModel):
    code: str = Field(min_length=1)
    category: Literal[
        "validation",
        "not_found",
        "conflict",
        "dependency_unmet",
        "upstream",
        "internal",
    ]
    message: str = Field(min_length=1)
    retryable: bool = False
    retry_after_ms: int | None = Field(default=None, ge=0)
    customer_safe_hint: str | None = None


class ResultMeta(ContractModel):
    trace_id: UUID
    latency_ms: int = Field(ge=0)
    model: str | None = None
    tokens: int = Field(default=0, ge=0)
    was_duplicate: bool | None = None


class AgentResultData(ContractModel):
    facts: list[Fact] = Field(default_factory=list, max_length=100)
    ui: Ui = Field(default_factory=Ui)
    suggested_actions: list[str] = Field(default_factory=list, max_length=20)


class AgentResult(ContractModel):
    ok: bool
    status: Literal["ok", "partial", "error"]
    task_id: str = Field(min_length=1)
    agent: str = Field(min_length=1)
    data: AgentResultData
    requires_human: bool = False
    handoff_reason: str | None = None
    error: AgentError | None = None
    meta: ResultMeta

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_data(cls, value: Any) -> Any:
        """Accept both the current nested ``data`` envelope and flat LG02 drafts."""
        if not isinstance(value, Mapping):
            return value
        payload = dict(value)
        nested = payload.get("data")
        flat_fields = ("facts", "ui", "suggested_actions")
        if nested is not None:
            if any(name in payload for name in flat_fields):
                raise ValueError("AgentResult cannot mix nested and flat data fields")
            return payload
        flattened = {name: payload.pop(name) for name in flat_fields if name in payload}
        payload["data"] = flattened
        return payload

    @property
    def facts(self) -> list[Fact]:
        """Expose facts directly to graph code while preserving the wire envelope."""
        return self.data.facts

    @property
    def ui(self) -> Ui:
        """Expose UI actions directly to graph code while preserving the wire envelope."""
        return self.data.ui

    @property
    def suggested_actions(self) -> list[str]:
        """Expose suggested actions directly to graph code."""
        return self.data.suggested_actions
