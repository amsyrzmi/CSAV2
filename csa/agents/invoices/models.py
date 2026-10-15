"""Data contracts for Invoice agent (LG13)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InvoiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["status", "list", "download_link"]
    invoice_id: str | None = Field(default=None, max_length=64)
    order_id: str | None = Field(default=None, max_length=64)
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def validate_action_fields(self) -> InvoiceInput:
        if self.action in {"status", "download_link"}:
            if not self.invoice_id and not self.order_id:
                raise ValueError("either invoice_id or order_id must be provided")
        return self
