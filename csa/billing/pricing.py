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
    """Round half-up to RM0.01 under ``_CTX``, not the ambient 28-digit context."""
    return value.quantize(_CENT, rounding=ROUND_HALF_UP, context=_CTX)


def _finite(value: Decimal, name: str) -> tuple[int, int]:
    """``(coefficient, exponent)`` of a finite ``value``, the coefficient signed."""
    if not value.is_finite():
        raise ValueError(f"{name} must be a finite number, not {value}")
    exponent = int(value.as_tuple().exponent)
    if value.adjusted() - exponent < _CTX.prec:
        # Fast path: moving the exponent to 0 rounds nothing while the digits fit ``_CTX``.
        return int(_CTX.scaleb(value, -exponent)), exponent
    sign, digits, _ = value.as_tuple()
    coefficient = int("".join(map(str, digits)))
    return (-coefficient if sign else coefficient), exponent


def _scaled(values: Sequence[Decimal], name: str) -> tuple[list[int], int]:
    """Every value as an exact int over one common power of ten: ``(ints, exponent)``.

    Each value equals ``int x 10**exponent``; no rounding, whatever the number of digits.
    """
    parts = [_finite(v, name) for v in values]
    exponent = min((e for c, e in parts if c), default=0)
    return [c * 10 ** (e - exponent) if c else 0 for c, e in parts], exponent


def _decimal(units: int, exponent: int) -> Decimal:
    """``units x 10**exponent`` built from its string, so no context rounds it."""
    return Decimal(f"{units}E{exponent}")


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

    ``None`` is open-ended. There is no default markup.

    ``NoMarkupTierError`` for a terminated stage, no tier set in force, or no covering row.
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

    Empty or all-zero input gives ``[]`` or zeros and needs ``total == 0``: money never vanishes.

    Pure integer arithmetic, so exact for any number of digits.

    CPU-bound and linear (a fraction of a second for 20,000 executions): callers in async code

    should still run it off the event loop when the list is large.
    """
    if total < 0:
        raise ValueError(f"total must not be negative, not {total}")
    if any(e < 0 for e in exact):
        raise ValueError("exact prices must not be negative")
    total_int, total_exp = _finite(total, "total")
    if total_exp >= -4:
        total_units = total_int * 10 ** (total_exp + 4)
    else:
        total_units, off_grid = divmod(total_int, 10 ** (-4 - total_exp))
        if off_grid:
            raise ValueError(f"total {total} is not a multiple of {_UNIT}")
    zero = _decimal(0, -4)
    ints, _ = _scaled(exact, "exact price")
    weight = sum(ints)
    if weight == 0:
        if total_units != 0:
            raise ValueError(f"nothing to allocate over: total is {total}")
        return [zero] * len(exact)
    shares, remainders = [], []
    for e in ints:
        share, remainder = divmod(e * total_units, weight)
        shares.append(share)
        remainders.append(remainder)
    leftover = total_units - sum(shares)
    if leftover:
        order = sorted(range(len(ints)), key=lambda i: (-remainders[i], i))
        for i in order[:leftover]:
            shares[i] += 1
    return [_decimal(u, -4) for u in shares]


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
    ints, exponent = _scaled(exact_prices, "exact price")
    ai_charge = _round_cent(_decimal(sum(ints), exponent))
    return Bill(platform_fee, ai_charge, platform_fee + ai_charge)
