"""Pricing, proration and price allocation: pure ``Decimal`` arithmetic, no I/O.

The rules (spec "The pricing rules"): a model call costs ``(prompt x input + completion x output)
/ 1,000,000`` USD times the FX rate in force, plus the markup of the tier its cycle's execution
count reaches. Execution prices stay exact (unrounded) until the cycle bill: the AI charge is the
sum of the exact prices rounded half-up to RM0.01, and ``allocate_prices`` then spreads that
rounded charge back over the executions in units of RM0.0001 so the displayed prices sum to it
exactly. The platform fee is prorated by days billed, rounded half-up to RM0.01.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Context, Decimal
from fractions import Fraction

from csa.billing.config import BillingConfig, fx_at, price_at, tiers_at
from csa.billing.plans import Bundle, Stage

__all__ = [
    "Bill",
    "NoMarkupTierError",
    "allocate_prices",
    "bill",
    "execution_price",
    "markup_pct",
    "prorated_fee",
    "raw_cost_rm",
    "tier_index",
]

_CENT = Decimal("0.01")
_UNIT = Decimal("0.0001")
_MILLION = 1_000_000
_FIRST_TIER_LIMIT = 2000
_SECOND_TIER_LIMIT = 10000
# Wide enough that no product or quotient here is ever rounded by the context.
_CTX = Context(prec=60)


class NoMarkupTierError(ValueError):
    """No markup tier row applies: the stage has no tiers, none is in force, or none covers it."""


def _round_cent(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def prorated_fee(full_fee: Decimal, days_billed: int, days_in_cycle: int) -> Decimal:
    """``full_fee x days_billed / days_in_cycle``, rounded half-up to RM0.01.

    A full cycle returns the fee itself; zero days gives ``0.00``.

    Needs ``0 <= days_billed <= days_in_cycle`` and a cycle with days, else ``ValueError``.
    """
    if days_in_cycle <= 0:
        raise ValueError(f"days_in_cycle must be positive, not {days_in_cycle}")
    if not 0 <= days_billed <= days_in_cycle:
        raise ValueError(f"days_billed must be 0 to {days_in_cycle}, not {days_billed}")
    if full_fee < 0:
        raise ValueError(f"full_fee must not be negative, not {full_fee}")
    if days_billed == days_in_cycle:
        return _round_cent(full_fee)
    return _round_cent(_CTX.divide(_CTX.multiply(full_fee, Decimal(days_billed)), days_in_cycle))


def tier_index(executions: int) -> int:
    """The tier an execution count reaches: 0 up to 2,000, 1 up to 10,000, otherwise 2."""
    if executions < 0:
        raise ValueError(f"executions must not be negative, not {executions}")
    if executions <= _FIRST_TIER_LIMIT:
        return 0
    if executions <= _SECOND_TIER_LIMIT:
        return 1
    return 2


def markup_pct(
    config: BillingConfig,
    stage: Stage,
    bundle: Bundle,
    executions: int,
    ts: datetime,
) -> Decimal:
    """The markup percent for ``executions`` in the cycle, from the tier set in force at ``ts``.

    The row is the one with the smallest ``up_to_executions`` at least ``executions``.

    ``None`` is open-ended. No default: ``NoMarkupTierError`` for a terminated stage,

    when no tier set is in force, or when no row covers the count.
    """
    if executions < 0:
        raise ValueError(f"executions must not be negative, not {executions}")
    if stage == "terminated":
        raise NoMarkupTierError("a terminated company has no markup: nothing is billed")
    tiers = tiers_at(config, stage, bundle, ts)
    if not tiers:
        raise NoMarkupTierError(
            f"no markup tiers for {stage}/{bundle} in force at {ts.isoformat()}"
        )
    for row in tiers:  # smallest limit first, the open-ended tier last
        if row.up_to_executions is None or executions <= row.up_to_executions:
            return row.markup_pct
    raise NoMarkupTierError(
        f"no markup tier for {stage}/{bundle} covers {executions} executions at {ts.isoformat()}"
    )


def raw_cost_rm(
    config: BillingConfig,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    ts: datetime,
) -> Decimal | None:
    """The raw cost of one call in RM, unrounded, or ``None`` when the model is unpriced at ``ts``.

    ``(prompt x input + completion x output) / 1,000,000`` USD, times the FX rate at ``ts``.

    ``NoFxRateError`` when there is no rate. Zero tokens cost ``0``, not ``None``.
    """
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("token counts must not be negative")
    price = price_at(config, provider, model, ts)
    if price is None:
        return None
    usd = _CTX.divide(
        _CTX.add(
            _CTX.multiply(Decimal(prompt_tokens), price.input_usd_per_mtok),
            _CTX.multiply(Decimal(completion_tokens), price.output_usd_per_mtok),
        ),
        _MILLION,
    )
    return _CTX.multiply(usd, fx_at(config, ts))


def execution_price(raw_rm: Decimal, markup_pct: Decimal) -> Decimal:
    """``raw_rm x (1 + markup_pct / 100)``, exact and unrounded (rounding happens in the bill)."""
    if raw_rm < 0 or markup_pct < 0:
        raise ValueError("raw cost and markup must not be negative")
    return _CTX.multiply(raw_rm, _CTX.add(Decimal(1), _CTX.divide(markup_pct, 100)))


def allocate_prices(exact: Sequence[Decimal], total: Decimal) -> list[Decimal]:
    """Spread ``total`` over the executions in proportion to ``exact``, summing to it exactly.

    Largest remainder in units of RM0.0001: each share is the floor of its exact proportion.

    Leftover units go one each to the largest fractional remainders (ties to the earlier index).

    ``total`` must be a non-negative multiple of 0.0001 (the AI charge, a multiple of 0.01, is).

    Empty input gives ``[]``; all-zero input gives zeros but needs ``total == 0``.
    """
    if total < 0:
        raise ValueError(f"total must not be negative, not {total}")
    if any(e < 0 for e in exact):
        raise ValueError("exact prices must not be negative")
    units, remainder = divmod(total, _UNIT)
    if remainder != 0:
        raise ValueError(f"total {total} is not a multiple of {_UNIT}")
    if not exact:
        return []
    weight = sum((Fraction(e) for e in exact), Fraction(0))
    if weight == 0:
        if total != 0:
            raise ValueError(f"all exact prices are zero but total is {total}")
        return [Decimal(0).scaleb(-4)] * len(exact)
    total_units = int(units)
    shares = [Fraction(e) * total_units / weight for e in exact]
    floors = [s.numerator // s.denominator for s in shares]
    leftover = total_units - sum(floors)
    order = sorted(range(len(shares)), key=lambda i: (-(shares[i] - floors[i]), i))
    for i in order[:leftover]:
        floors[i] += 1
    return [Decimal(u).scaleb(-4) for u in floors]


@dataclass(frozen=True)
class Bill:
    """One cycle's bill in RM: the platform fee, the AI charge, and their sum."""

    platform_fee: Decimal
    ai_charge: Decimal
    total: Decimal


def bill(*, fee: Decimal, exact_prices: Sequence[Decimal]) -> Bill:
    """The cycle bill: fee and AI charge each rounded half-up to RM0.01, total their sum.

    The AI charge is the rounded sum of the exact execution prices, whatever is displayed per call.
    """
    if fee < 0:
        raise ValueError(f"fee must not be negative, not {fee}")
    if any(p < 0 for p in exact_prices):
        raise ValueError("exact prices must not be negative")
    platform_fee = _round_cent(fee)
    ai_charge = _round_cent(sum(exact_prices, Decimal(0)))
    return Bill(platform_fee, ai_charge, platform_fee + ai_charge)
