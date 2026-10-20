"""Selected v202309 provider fields, not normalized commerce or browser contracts."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _TikTokSnapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, hide_input_in_errors=True)


class TikTokPaymentSnapshot(_TikTokSnapshot):
    total_amount: Decimal = Field(ge=0, allow_inf_nan=False)
    currency: str = Field(min_length=3, max_length=3)


class TikTokLineItemSnapshot(_TikTokSnapshot):
    """A distinct provider line; sale_price does not assert a quantity or unit price."""

    id: str = Field(min_length=1, max_length=100)
    sku_id: str = Field(min_length=1, max_length=100)
    seller_sku: str | None = Field(default=None, max_length=255)
    product_name: str = Field(min_length=1, max_length=255)
    sale_price: Decimal = Field(ge=0, allow_inf_nan=False)


class TikTokOrderSnapshot(_TikTokSnapshot):
    id: str = Field(min_length=1, max_length=100)
    status: str = Field(min_length=1, max_length=100)
    payment: TikTokPaymentSnapshot
    line_items: tuple[TikTokLineItemSnapshot, ...]

    @model_validator(mode="after")
    def require_distinct_lines(self) -> TikTokOrderSnapshot:
        ids = [line.id for line in self.line_items]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate provider line identity")
        return self


class TikTokOrderSnapshotPage(_TikTokSnapshot):
    items: tuple[TikTokOrderSnapshot, ...]
    next_page_token: str | None = Field(default=None, min_length=1, max_length=2048)
