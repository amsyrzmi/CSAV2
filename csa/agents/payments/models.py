"""What the planner may ask the payment agent to do.

The planner is shown this schema as it stands, so the descriptions are its instructions. There is
no field for an amount, an account number or an invoice id: the server finds the customer's own
invoices from this chat and inserts every amount, account number and reference itself.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PaymentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: Literal["instructions", "status", "claim_paid"] = Field(
        description=(
            "instructions: how to pay the customer's own open invoices (bank account, DuitNow, "
            "the reference to use). status: whether a payment of theirs was recorded. "
            "claim_paid: the customer says they have paid; it is noted for staff to confirm and "
            "nothing is marked paid. The server finds the customer's invoices from this chat and "
            "writes every amount, account number and reference itself: never write one."
        )
    )
    reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=12,
        description=(
            "Only an invoice reference the customer gave themselves (for example AB2C-DE7), or "
            "the one they chose from those the last reply listed, copied as written. Leave it "
            "out otherwise; never make one up."
        ),
    )
    note: str | None = Field(
        default=None,
        min_length=1,
        max_length=300,
        description=(
            "claim_paid only: what the customer said about their payment (when, from which "
            "bank), in their own words. Never an amount or account number they did not write."
        ),
    )

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_missing(cls, value: object) -> object:
        """A model fills an unused field with an empty string as often as it leaves it out."""
        if isinstance(value, str) and "\x00" in value:
            raise ValueError("NUL characters cannot be persisted")
        return None if isinstance(value, str) and not value.strip() else value
