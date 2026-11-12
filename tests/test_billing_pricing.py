"""Pricing, proration and price allocation (``csa.billing.pricing``): pure, no database.

The seeded markup table is taken from ``tests.test_billing_config_schema`` (which asserts the
migration seeds exactly those values), so the numbers live in one place. All data is synthetic.
"""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import get_args

import pytest
from csa.billing.config import BillingConfig, FeeRow, FxRow, PriceRow, TierRow
from csa.billing.cycles import Cycle, days_in
from csa.billing.plans import Bundle, Stage
from csa.billing.pricing import (
    Bill,
    NoMarkupTierError,
    allocate_prices,
    bill,
    execution_price,
    markup_pct,
    prorated_fee,
    raw_cost_rm,
    tier_index,
)

from tests.test_billing_config_schema import MARKUPS, TIER_LIMITS

SEED_DATE = datetime(2020, 1, 1, tzinfo=UTC)
NOW = datetime(2026, 11, 12, tzinfo=UTC)
CENT = Decimal("0.01")
UNIT = Decimal("0.0001")
PILOT_FEE = Decimal("99.99")
COLLABORATOR_FEE = Decimal("119.99")
STAGES: tuple[Stage, ...] = get_args(Stage)
BUNDLES: tuple[Bundle, ...] = get_args(Bundle)
BILLED_STAGES: tuple[Stage, ...] = ("pilot", "collaborator")


def _config() -> BillingConfig:
    tiers = tuple(
        TierRow(stage, bundle, limit, Decimal(pct), SEED_DATE)
        for (stage, bundle), pcts in MARKUPS.items()
        for limit, pct in zip(TIER_LIMITS, pcts, strict=True)
    )
    return BillingConfig(
        prices=(
            PriceRow("groq", "m", Decimal("0.15"), Decimal("0.60"), SEED_DATE),
            PriceRow(
                "groq", "m", Decimal("0.30"), Decimal("1.20"), datetime(2026, 6, 1, tzinfo=UTC)
            ),
        ),
        fx=(
            FxRow(Decimal("4.0840"), SEED_DATE),
            FxRow(Decimal("4.5000"), datetime(2026, 6, 1, tzinfo=UTC)),
        ),
        fees=(
            FeeRow("pilot", PILOT_FEE, SEED_DATE),
            FeeRow("collaborator", COLLABORATOR_FEE, SEED_DATE),
        ),
        tiers=tiers,
    )


def _round_cent(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# --- tier_index and markup_pct -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("executions", "index"),
    [(0, 0), (1, 0), (2000, 0), (2001, 1), (10000, 1), (10001, 2), (500000, 2)],
)
def test_tier_index(executions: int, index: int) -> None:
    assert tier_index(executions) == index


def test_tier_index_refuses_negative() -> None:
    with pytest.raises(ValueError):
        tier_index(-1)


def test_markup_matches_the_seed_table_for_every_cell() -> None:
    config = _config()
    samples = (1, 1500, 5000, 20000)  # one inside each tier, and the first-tier floor
    expected_tier = (0, 0, 1, 2)
    cells = 0
    for stage in BILLED_STAGES:
        for bundle in BUNDLES:
            pcts = MARKUPS[(stage, bundle)]
            for executions, tier in zip(samples, expected_tier, strict=True):
                got = markup_pct(config, stage, bundle, executions, NOW)
                assert got == Decimal(pcts[tier]), (stage, bundle, executions)
                cells += 1
    assert cells == 2 * 3 * 4


def test_markup_tier_boundaries_for_every_stage_and_bundle() -> None:
    config = _config()
    for stage in BILLED_STAGES:
        for bundle in BUNDLES:
            first, second, third = (Decimal(p) for p in MARKUPS[(stage, bundle)])
            assert markup_pct(config, stage, bundle, 2000, NOW) == first
            assert markup_pct(config, stage, bundle, 2001, NOW) == second
            assert markup_pct(config, stage, bundle, 10000, NOW) == second
            assert markup_pct(config, stage, bundle, 10001, NOW) == third


def test_markup_uses_the_tier_set_in_force_at_ts() -> None:
    base = _config()
    later = datetime(2026, 7, 1, tzinfo=UTC)
    newer = tuple(
        TierRow("pilot", "individual", limit, Decimal(pct), later)
        for limit, pct in zip(TIER_LIMITS, ("60", "55", "50"), strict=True)
    )
    config = BillingConfig(base.prices, base.fx, base.fees, base.tiers + newer)
    assert markup_pct(config, "pilot", "individual", 1, datetime(2026, 6, 30, tzinfo=UTC)) == 75
    assert markup_pct(config, "pilot", "individual", 1, later) == 60


def test_markup_refuses_a_terminated_stage() -> None:
    with pytest.raises(NoMarkupTierError):
        markup_pct(_config(), "terminated", "individual", 10, NOW)


def test_markup_refuses_when_no_tier_is_in_force() -> None:
    with pytest.raises(NoMarkupTierError):
        markup_pct(_config(), "pilot", "individual", 10, datetime(2019, 1, 1, tzinfo=UTC))


def test_markup_refuses_when_no_row_covers_the_executions() -> None:
    capped = BillingConfig(
        (),
        (),
        (),
        (TierRow("pilot", "individual", 2000, Decimal("75"), SEED_DATE),),
    )
    assert markup_pct(capped, "pilot", "individual", 2000, NOW) == 75
    with pytest.raises(NoMarkupTierError):
        markup_pct(capped, "pilot", "individual", 2001, NOW)


def test_markup_refuses_negative_executions() -> None:
    with pytest.raises(ValueError):
        markup_pct(_config(), "pilot", "individual", -1, NOW)


# --- raw_cost_rm and execution_price -------------------------------------------------------------


def test_raw_cost_is_tokens_times_price_times_fx() -> None:
    # (1,000,000 x $0.15 + 1,000,000 x $0.60) / 1,000,000 = $0.75; x 4.0840 = RM3.0630
    cost = raw_cost_rm(
        _config(), "groq", "m", 1_000_000, 1_000_000, datetime(2026, 1, 1, tzinfo=UTC)
    )
    assert cost == Decimal("0.75") * Decimal("4.0840") == Decimal("3.0630")


def test_raw_cost_uses_the_price_and_fx_in_force_at_ts() -> None:
    # After 2026-06-01: $0.30 in, $1.20 out, FX 4.5000: $1.50 x 4.5 = RM6.75
    cost = raw_cost_rm(_config(), "groq", "m", 1_000_000, 1_000_000, NOW)
    assert cost == Decimal("6.75")


def test_raw_cost_keeps_full_precision() -> None:
    # 1 prompt token at $0.15 per million, FX 4.0840: 0.15 x 4.0840 / 1e6, not rounded
    cost = raw_cost_rm(_config(), "groq", "m", 1, 0, datetime(2026, 1, 1, tzinfo=UTC))
    assert cost == Decimal("0.0000006126")


def test_raw_cost_of_zero_tokens_is_zero_not_none() -> None:
    assert raw_cost_rm(_config(), "groq", "m", 0, 0, NOW) == 0


def test_unpriced_model_gives_none() -> None:
    assert raw_cost_rm(_config(), "groq", "other", 10, 10, NOW) is None
    assert raw_cost_rm(_config(), "gemini", "m", 10, 10, NOW) is None
    # Priced from 2020 only: a call before that is unpriced.
    assert raw_cost_rm(_config(), "groq", "m", 10, 10, datetime(2019, 1, 1, tzinfo=UTC)) is None


def test_execution_price_is_exact_and_unrounded() -> None:
    assert execution_price(Decimal("0.016737"), Decimal("70")) == Decimal("0.0284529")
    assert (
        execution_price(Decimal("0.0167365"), Decimal("77.5"))
        == Decimal("0.0167365") * Decimal("1.775")
        == Decimal("0.0297072875")
    )
    assert execution_price(Decimal("2"), Decimal("0")) == Decimal("2")


# --- prorated_fee --------------------------------------------------------------------------------


def test_prorated_fee_join_on_15_aug_is_21_of_31_days() -> None:
    # 15 Aug to 5 Sep is 21 days of the 31-day cycle 5 Aug to 5 Sep: 99.99 x 21 / 31 = 67.7351...
    cycle = Cycle(date(2026, 8, 5), date(2026, 9, 5))
    assert days_in(cycle) == 31
    assert (date(2026, 9, 5) - date(2026, 8, 15)).days == 21
    assert prorated_fee(PILOT_FEE, 21, 31) == Decimal("67.74")
    assert prorated_fee(PILOT_FEE, 21, 31) == _round_cent(PILOT_FEE * 21 / 31)


@pytest.mark.parametrize("days", [28, 30, 31])
def test_prorated_fee_full_cycle_is_exact(days: int) -> None:
    assert prorated_fee(PILOT_FEE, days, days) == PILOT_FEE
    assert prorated_fee(COLLABORATOR_FEE, days, days) == COLLABORATOR_FEE


@pytest.mark.parametrize("days_in_cycle", [28, 30, 31])
def test_prorated_fee_every_day_count_rounds_half_up(days_in_cycle: int) -> None:
    for fee in (PILOT_FEE, COLLABORATOR_FEE):
        for billed in range(days_in_cycle + 1):
            assert prorated_fee(fee, billed, days_in_cycle) == _round_cent(
                fee * billed / days_in_cycle
            )


def test_prorated_fee_termination_before_the_5th() -> None:
    # Billed 5 Sep to 20 Sep: 15 of the 30 days of the cycle 5 Sep to 5 Oct.
    cycle = Cycle(date(2026, 9, 5), date(2026, 10, 5))
    billed = (date(2026, 9, 20) - date(2026, 9, 5)).days
    assert (billed, days_in(cycle)) == (15, 30)
    assert prorated_fee(PILOT_FEE, billed, days_in(cycle)) == Decimal("50.00")  # 49.995 up
    assert prorated_fee(COLLABORATOR_FEE, billed, days_in(cycle)) == Decimal("60.00")  # 59.995 up


def test_prorated_fee_rounds_exact_halves_up() -> None:
    assert prorated_fee(Decimal("0.05"), 1, 2) == Decimal("0.03")
    assert prorated_fee(Decimal("0.01"), 1, 2) == Decimal("0.01")
    assert prorated_fee(Decimal("0.01"), 1, 3) == Decimal("0.00")


def test_prorated_fee_zero_days_is_zero() -> None:
    assert prorated_fee(PILOT_FEE, 0, 30) == Decimal("0.00")
    assert str(prorated_fee(PILOT_FEE, 0, 30)) == "0.00"


@pytest.mark.parametrize(
    ("days_billed", "days_in_cycle"), [(-1, 30), (31, 30), (0, 0), (1, 0), (0, -5)]
)
def test_prorated_fee_refuses_bad_days(days_billed: int, days_in_cycle: int) -> None:
    with pytest.raises(ValueError):
        prorated_fee(PILOT_FEE, days_billed, days_in_cycle)


def test_prorated_fee_refuses_a_negative_fee() -> None:
    with pytest.raises(ValueError):
        prorated_fee(Decimal("-1"), 1, 30)


# --- allocate_prices -----------------------------------------------------------------------------


def _check_allocation(exact: list[Decimal], total: Decimal) -> list[Decimal]:
    out = allocate_prices(exact, total)
    assert len(out) == len(exact)
    assert sum(out, Decimal(0)) == total
    assert all(p >= 0 for p in out)
    assert all(p % UNIT == 0 for p in out)
    return out


def test_allocate_sums_exactly_for_200_seeded_random_cases() -> None:
    rng = random.Random(20261112)
    for _ in range(200):
        n = rng.randint(1, 60)
        exact = [Decimal(rng.randint(0, 10**9)).scaleb(-rng.randint(4, 9)) for _ in range(n)]
        if not any(exact):
            exact[0] = Decimal("0.0001")
        total = Decimal(rng.randint(0, 5_000_000)).scaleb(-2)
        _check_allocation(exact, total)


def test_allocate_is_proportional_to_exact() -> None:
    out = _check_allocation([Decimal(1), Decimal(2), Decimal(3)], Decimal("6.00"))
    assert out == [Decimal("1.0000"), Decimal("2.0000"), Decimal("3.0000")]


def test_allocate_exact_prices_that_already_sum_to_total_are_kept() -> None:
    exact = [Decimal("0.0284"), Decimal("0.0100"), Decimal("0.0016")]
    assert _check_allocation(exact, Decimal("0.04")) == exact


def test_allocate_largest_remainder_goes_first() -> None:
    # Shares in units of 0.0001 out of 1.00 (10,000 units): 3333.33, 3333.33, 3333.33 -> one spare
    # unit. Remainders are equal, so the earliest index takes it.
    third = [Decimal(1), Decimal(1), Decimal(1)]
    assert _check_allocation(third, Decimal("1.00")) == [
        Decimal("0.3334"),
        Decimal("0.3333"),
        Decimal("0.3333"),
    ]
    # 100 units over 1 : 1 : 1.5 are 28.57, 28.57, 42.86: floors 28, 28, 42 leave two spare units,
    # which go to the largest remainders (.86 then .57, the earlier of the tied pair).
    skewed = [Decimal("1"), Decimal("1"), Decimal("1.5")]
    out = _check_allocation(skewed, Decimal("0.01"))
    assert out == [Decimal("0.0029"), Decimal("0.0028"), Decimal("0.0043")]


def test_allocate_ties_go_to_the_earlier_index() -> None:
    out = _check_allocation([Decimal(1)] * 4, Decimal("0.01"))  # 100 units over 4: 25 each
    assert out == [Decimal("0.0025")] * 4
    out = _check_allocation([Decimal(1)] * 3, Decimal("0.01"))  # 33, 33, 33 plus one spare
    assert out == [Decimal("0.0034"), Decimal("0.0033"), Decimal("0.0033")]
    out = _check_allocation([Decimal(1)] * 7, Decimal("0.01"))  # 14 each + 2 spare units
    assert out == [Decimal("0.0015")] * 2 + [Decimal("0.0014")] * 5


def test_allocate_one_execution_takes_the_whole_total() -> None:
    assert allocate_prices([Decimal("0.0123456")], Decimal("0.01")) == [Decimal("0.0100")]
    assert allocate_prices([Decimal("7")], Decimal("594.15")) == [Decimal("594.1500")]


def test_allocate_zero_usage() -> None:
    assert allocate_prices([], Decimal("0.00")) == []
    assert allocate_prices([Decimal(0), Decimal(0)], Decimal("0.00")) == [Decimal(0), Decimal(0)]


def test_allocate_all_zero_with_a_total_is_refused() -> None:
    with pytest.raises(ValueError):
        allocate_prices([Decimal(0), Decimal(0)], Decimal("0.01"))


def test_allocate_zero_among_nonzero_gets_nothing() -> None:
    out = _check_allocation([Decimal(0), Decimal(5), Decimal(0)], Decimal("1.23"))
    assert out == [Decimal("0.0000"), Decimal("1.2300"), Decimal("0.0000")]


def test_allocate_refuses_a_total_off_the_grid() -> None:
    with pytest.raises(ValueError):
        allocate_prices([Decimal(1)], Decimal("0.00005"))
    with pytest.raises(ValueError):
        allocate_prices([Decimal(1), Decimal(1)], Decimal("1.00001"))


def test_allocate_refuses_negative_values() -> None:
    with pytest.raises(ValueError):
        allocate_prices([Decimal(1), Decimal(-1)], Decimal("1.00"))
    with pytest.raises(ValueError):
        allocate_prices([Decimal(1)], Decimal("-0.01"))


# --- bill ----------------------------------------------------------------------------------------


def test_bill_rounds_fee_and_ai_charge_separately() -> None:
    result = bill(fee=Decimal("99.994"), exact_prices=[Decimal("0.0025"), Decimal("0.0025")])
    assert result == Bill(Decimal("99.99"), Decimal("0.01"), Decimal("100.00"))  # 0.005 rounds up


def test_bill_total_is_the_sum_of_the_rounded_figures() -> None:
    result = bill(fee=Decimal("99.99"), exact_prices=[Decimal("0.004")])
    assert (result.platform_fee, result.ai_charge, result.total) == (
        Decimal("99.99"),
        Decimal("0.00"),
        Decimal("99.99"),
    )
    result = bill(fee=Decimal("0.004"), exact_prices=[Decimal("0.004")])
    assert result.total == Decimal("0.00")  # 0.008 unrounded would be 0.01; the figures give 0.00
    assert result.total == result.platform_fee + result.ai_charge


def test_bill_with_no_executions() -> None:
    result = bill(fee=PILOT_FEE, exact_prices=[])
    assert result == Bill(Decimal("99.99"), Decimal("0.00"), Decimal("99.99"))


def test_bill_figures_carry_two_decimals() -> None:
    result = bill(fee=Decimal("0"), exact_prices=[])
    assert str(result.platform_fee) == "0.00"
    assert str(result.ai_charge) == "0.00"
    assert str(result.total) == "0.00"


def test_bill_refuses_negative_inputs() -> None:
    with pytest.raises(ValueError):
        bill(fee=Decimal("-1"), exact_prices=[])
    with pytest.raises(ValueError):
        bill(fee=Decimal("1"), exact_prices=[Decimal("-0.0001")])


# --- the workbook vectors ------------------------------------------------------------------------

TOLERANCE = Decimal("0.01")


def _vector(
    stage: Stage, bundle: Bundle, executions: int, raw: str, fee: Decimal
) -> tuple[Bill, list[Decimal], Decimal]:
    """Split ``raw`` evenly over ``executions`` and price each at the tier its count reaches."""
    config = _config()
    pct = markup_pct(config, stage, bundle, executions, NOW)
    each = Decimal(raw) / executions
    assert each * executions == Decimal(raw)  # the even split is exact for these figures
    exact = [execution_price(each, pct)] * executions
    return bill(fee=fee, exact_prices=exact), exact, pct


def test_vector_pilot_individual_10000_executions() -> None:
    result, exact, pct = _vector("pilot", "individual", 10000, "167.37", PILOT_FEE)
    assert pct == Decimal("70")
    assert result.ai_charge == Decimal("284.53")  # 167.37 x 1.70 = 284.529
    assert result.total == Decimal("384.52")
    assert abs(result.ai_charge - Decimal("284.52")) <= TOLERANCE
    assert abs(result.total - Decimal("384.51")) <= TOLERANCE
    allocated = _check_allocation(exact, result.ai_charge)
    assert sum(allocated, Decimal(0)) == result.ai_charge


def test_vector_collaborator_complete_20000_executions() -> None:
    result, exact, pct = _vector("collaborator", "complete", 20000, "334.73", COLLABORATOR_FEE)
    assert pct == Decimal("77.5")
    assert result.ai_charge == Decimal("594.15")  # 334.73 x 1.775 = 594.14575
    assert result.total == Decimal("714.14")
    assert abs(result.ai_charge - Decimal("594.15")) <= TOLERANCE
    assert abs(result.total - Decimal("714.14")) <= TOLERANCE
    assert sum(_check_allocation(exact, result.ai_charge), Decimal(0)) == result.ai_charge


def test_vector_pilot_individual_1000_executions() -> None:
    result, exact, pct = _vector("pilot", "individual", 1000, "16.73", PILOT_FEE)
    assert pct == Decimal("75")
    assert result.ai_charge == Decimal("29.28")  # 16.73 x 1.75 = 29.2775
    assert result.total == Decimal("129.27")
    assert abs(result.ai_charge - Decimal("29.28")) <= TOLERANCE
    assert abs(result.total - Decimal("129.27")) <= TOLERANCE
    assert sum(_check_allocation(exact, result.ai_charge), Decimal(0)) == result.ai_charge
