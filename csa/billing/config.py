"""Billing configuration: the effective-dated price, FX, fee and markup tables, and the rules for
changing them.

``load_config`` reads every row of the four append-only tables (migration 0571). The lookups are
pure: they take the loaded ``BillingConfig`` and a timestamp, and pick the row with the latest
``effective_from`` at or before it. A missing row is an error, never a default: a price is
``None`` (the caller shows the call as unpriced), and a missing FX rate or fee raises.
``check_new_price_date`` is the shadow rule that keeps closed billing cycles stable.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import psycopg

from csa.core import db

__all__ = [
    "BillingConfig",
    "BillingConfigError",
    "FeeRow",
    "FxRow",
    "NoFeeError",
    "NoFxRateError",
    "PriceRow",
    "ShadowRuleError",
    "TierRow",
    "check_new_price_date",
    "fee_for",
    "fx_at",
    "load_config",
    "price_at",
    "tiers_at",
]

# Billing days are Kuala Lumpur days (cycles run 5th 00:00 to 5th 00:00 there).
BILLING_TZ = ZoneInfo("Asia/Kuala_Lumpur")
_MAX_DAYS_AHEAD = 1


class BillingConfigError(Exception):
    """A billing configuration lookup or write was refused."""


class NoFxRateError(BillingConfigError):
    """No USD to MYR rate is in force at the requested time."""


class NoFeeError(BillingConfigError):
    """No platform fee row is in force for the stage at the requested time."""


class ShadowRuleError(BillingConfigError, ValueError):
    """A new price or FX row's ``effective_from`` would change a priced range."""


@dataclass(frozen=True)
class PriceRow:
    """USD per million tokens for one provider and model, from ``effective_from``."""

    provider: str
    model: str
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    effective_from: datetime


@dataclass(frozen=True)
class FxRow:
    """The USD to MYR rate from ``effective_from``."""

    usd_to_myr: Decimal
    effective_from: datetime


@dataclass(frozen=True)
class FeeRow:
    """The monthly platform fee in RM for a stage, from ``effective_from``."""

    stage: str
    monthly_fee_rm: Decimal
    effective_from: datetime


@dataclass(frozen=True)
class TierRow:
    """One markup tier: ``markup_pct`` percent up to ``up_to_executions`` (``None``: open-ended)."""

    stage: str
    bundle: str
    up_to_executions: int | None
    markup_pct: Decimal
    effective_from: datetime


@dataclass(frozen=True)
class BillingConfig:
    """All four tables, as loaded. Row order is not significant."""

    prices: tuple[PriceRow, ...]
    fx: tuple[FxRow, ...]
    fees: tuple[FeeRow, ...]
    tiers: tuple[TierRow, ...]


async def load_config(conn: psycopg.AsyncConnection[Any]) -> BillingConfig:
    """Read every row of the four tables on ``conn``; numerics come back as ``Decimal``."""
    prices = await db.q.fetch_all("billing/prices_list.sql", conn=conn)
    fx = await db.q.fetch_all("billing/fx_list.sql", conn=conn)
    fees = await db.q.fetch_all("billing/fees_list.sql", conn=conn)
    tiers = await db.q.fetch_all("billing/tiers_list.sql", conn=conn)
    return BillingConfig(
        prices=tuple(PriceRow(**row) for row in prices),
        fx=tuple(FxRow(**row) for row in fx),
        fees=tuple(FeeRow(**row) for row in fees),
        tiers=tuple(TierRow(**row) for row in tiers),
    )


def price_at(config: BillingConfig, provider: str, model: str, ts: datetime) -> PriceRow | None:
    """The price in force for ``provider`` and ``model`` at ``ts``, or ``None`` if unpriced."""
    rows = [
        r
        for r in config.prices
        if r.provider == provider and r.model == model and r.effective_from <= ts
    ]
    return max(rows, key=lambda r: r.effective_from, default=None)


def fx_at(config: BillingConfig, ts: datetime) -> Decimal:
    """The USD to MYR rate in force at ``ts``. Raises ``NoFxRateError`` when there is none."""
    rows = [r for r in config.fx if r.effective_from <= ts]
    if not rows:
        raise NoFxRateError(f"no USD to MYR rate in force at {ts.isoformat()}")
    return max(rows, key=lambda r: r.effective_from).usd_to_myr


def fee_for(config: BillingConfig, stage: str, ts: datetime) -> Decimal:
    """The monthly platform fee in RM for ``stage`` at ``ts``. Raises ``NoFeeError`` if none."""
    rows = [r for r in config.fees if r.stage == stage and r.effective_from <= ts]
    if not rows:
        raise NoFeeError(f"no platform fee for stage {stage!r} in force at {ts.isoformat()}")
    return max(rows, key=lambda r: r.effective_from).monthly_fee_rm


def tiers_at(config: BillingConfig, stage: str, bundle: str, ts: datetime) -> tuple[TierRow, ...]:
    """The markup tier set in force for ``stage`` and ``bundle`` at ``ts``, smallest tier first.

    A set is all the rows sharing the latest ``effective_from`` at or before ``ts``; the
    open-ended tier (``up_to_executions`` ``None``) is last. Empty when none is in force.
    """
    rows = [
        r
        for r in config.tiers
        if r.stage == stage and r.bundle == bundle and r.effective_from <= ts
    ]
    if not rows:
        return ()
    latest = max(r.effective_from for r in rows)
    current = [r for r in rows if r.effective_from == latest]
    return tuple(
        sorted(
            current,
            key=lambda r: (r.up_to_executions is None, r.up_to_executions or 0),
        )
    )


def _billing_day(moment: datetime, name: str) -> date:
    if moment.tzinfo is None:
        raise ValueError(f"{name} must carry a timezone")
    return moment.astimezone(BILLING_TZ).date()


def check_new_price_date(
    existing_dates: Sequence[datetime],
    effective_from: datetime,
    now: datetime,
) -> None:
    """The shadow rule for a new price or FX row; raises ``ShadowRuleError`` when it is refused.

    ``existing_dates`` are the ``effective_from`` values already held for that model (or for
    the FX table). A new row may be back-dated only to a date before the earliest existing one,
    which fills a gap of unpriced calls and changes no priced range. Otherwise it must start
    today or later, since an earlier date would re-price a range that is already priced.
    Whatever the case, it may be at most one day ahead. Days are Kuala Lumpur days.
    """
    dates = list(existing_dates)
    for existing in dates:
        _billing_day(existing, "existing date")
    start = _billing_day(effective_from, "effective_from")
    today = _billing_day(now, "now")
    if start > today + timedelta(days=_MAX_DAYS_AHEAD):
        raise ShadowRuleError(
            f"effective_from {start.isoformat()} is more than {_MAX_DAYS_AHEAD} day ahead"
            f" of today ({today.isoformat()})"
        )
    if not dates or effective_from < min(dates):
        return
    if start < today:
        raise ShadowRuleError(
            f"effective_from {start.isoformat()} falls inside an already priced range;"
            f" use today ({today.isoformat()}) or later, or a date before"
            f" {min(dates).astimezone(BILLING_TZ).date().isoformat()}"
        )
