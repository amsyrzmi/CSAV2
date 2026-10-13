"""Server-side payloads for actions requiring approval (LG02/LG07)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import Field

from csa.contracts.envelope import ContractModel


class OrderApprovalLine(ContractModel):
    qty: int = Field(gt=0)
    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    unit_price: Decimal = Field(ge=0)


class OrderApprovalPayload(ContractModel):
    order_id: str = Field(min_length=1)
    total: Decimal = Field(ge=0)
    currency: str = Field(min_length=1, max_length=3)
    lines: list[OrderApprovalLine] = Field(min_length=1, max_length=100)
    customer_name: str | None = Field(default=None, min_length=1, max_length=256)
