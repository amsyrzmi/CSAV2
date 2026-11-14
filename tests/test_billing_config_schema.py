"""Billing configuration: migration 0571 (effective-dated price, FX, fee and markup tables) and the
pure lookups and shadow rule in ``csa.billing.config``. The database tests run on the disposable
Docker Postgres; the lookup and shadow-rule tests are pure. All data is synthetic.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION
from csa.billing.config import (
    BillingConfig,
    BillingConfigError,
    FeeRow,
    FxRow,
    NoFeeError,
    NoFxRateError,
    PriceRow,
    ShadowRuleError,
    TierRow,
    check_new_price_date,
    fee_for,
    fx_at,
    load_config,
    price_at,
    tiers_at,
)
from csa.core.settings import Settings
from psycopg import errors
from psycopg.rows import dict_row

from tests.conftest import requires_docker
from tests.test_bookings_schema import DictConnection, _owner

MIGRATION = "0571"
ROOT = Path(__file__).resolve().parent.parent
TABLES = ("ai_model_prices", "billing_fx_rates", "billing_stage_fees", "billing_markup_tiers")
SEED_DATE = datetime(2020, 1, 1, tzinfo=UTC)

# The owner's markup table (global constraints): (stage, bundle) -> percent per tier.
MARKUPS = {
    ("pilot", "individual"): ("75", "70", "65"),
    ("pilot", "curated"): ("70", "65", "65"),
    ("pilot", "complete"): ("67.5", "65", "65"),
    ("collaborator", "individual"): ("85", "80", "77.5"),
    ("collaborator", "curated"): ("80", "77.5", "77.5"),
    ("collaborator", "complete"): ("77.5", "77.5", "77.5"),
}
TIER_LIMITS = (2000, 10000, None)


def _sql(text: str) -> LiteralString:
    """Test SQL built from constants and placeholders, never from input."""
    return cast(LiteralString, text)


def _at(*ymd: int) -> datetime:
    return datetime(*ymd, tzinfo=UTC)


def _price(model: str, day: datetime, inp: str = "1", out: str = "2") -> PriceRow:
    return PriceRow("groq", model, Decimal(inp), Decimal(out), day)


# ---------------------------------------------------------------------------------------------
# Pure lookups
# ---------------------------------------------------------------------------------------------


def test_price_at_picks_the_row_in_force_at_the_time() -> None:
    old, new = _price("m", _at(2026, 1, 1), "1", "2"), _price("m", _at(2026, 6, 1), "3", "4")
    other = PriceRow("gemini", "m", Decimal("9"), Decimal("9"), _at(2026, 1, 1))
    config = BillingConfig(prices=(new, other, old), fx=(), fees=(), tiers=())
    assert price_at(config, "groq", "m", _at(2026, 3, 1)) == old
    assert price_at(config, "groq", "m", _at(2026, 6, 1)) == new  # the effective instant itself
    assert price_at(config, "groq", "m", _at(2027, 1, 1)) == new
    assert price_at(config, "gemini", "m", _at(2026, 3, 1)) == other


def test_price_at_is_none_before_the_first_row_and_for_an_unknown_model() -> None:
    config = BillingConfig(prices=(_price("m", _at(2026, 1, 1)),), fx=(), fees=(), tiers=())
    assert price_at(config, "groq", "m", _at(2025, 12, 31)) is None
    assert price_at(config, "groq", "other", _at(2026, 3, 1)) is None
    assert price_at(config, "gemini", "m", _at(2026, 3, 1)) is None


def test_fx_at_picks_the_row_in_force_and_is_loud_without_one() -> None:
    config = BillingConfig(
        prices=(),
        fx=(FxRow(Decimal("4.5"), _at(2026, 6, 1)), FxRow(Decimal("4.0"), _at(2026, 1, 1))),
        fees=(),
        tiers=(),
    )
    assert fx_at(config, _at(2026, 3, 1)) == Decimal("4.0")
    assert fx_at(config, _at(2026, 6, 1)) == Decimal("4.5")
    with pytest.raises(NoFxRateError):
        fx_at(config, _at(2025, 12, 31))
    with pytest.raises(NoFxRateError):
        fx_at(BillingConfig(prices=(), fx=(), fees=(), tiers=()), _at(2026, 1, 1))
    assert issubclass(NoFxRateError, BillingConfigError)


def test_fee_for_picks_the_row_in_force_per_stage() -> None:
    config = BillingConfig(
        prices=(),
        fx=(),
        fees=(
            FeeRow("pilot", Decimal("99.99"), _at(2020, 1, 1)),
            FeeRow("pilot", Decimal("109.99"), _at(2026, 7, 1)),
            FeeRow("collaborator", Decimal("119.99"), _at(2020, 1, 1)),
        ),
        tiers=(),
    )
    assert fee_for(config, "pilot", _at(2026, 6, 30)) == Decimal("99.99")
    assert fee_for(config, "pilot", _at(2026, 7, 1)) == Decimal("109.99")
    assert fee_for(config, "collaborator", _at(2026, 7, 1)) == Decimal("119.99")
    with pytest.raises(NoFeeError):
        fee_for(config, "terminated", _at(2026, 7, 1))
    with pytest.raises(NoFeeError):
        fee_for(config, "pilot", _at(2019, 1, 1))


def test_tiers_at_returns_the_set_in_force_ordered_with_the_open_tier_last() -> None:
    def tier(limit: int | None, pct: str, day: datetime) -> TierRow:
        return TierRow("pilot", "individual", limit, Decimal(pct), day)

    config = BillingConfig(
        prices=(),
        fx=(),
        fees=(),
        tiers=(
            tier(None, "65", _at(2020, 1, 1)),
            tier(10000, "70", _at(2020, 1, 1)),
            tier(2000, "75", _at(2020, 1, 1)),
            tier(None, "60", _at(2026, 7, 1)),
            tier(10000, "62", _at(2026, 7, 1)),
            tier(2000, "64", _at(2026, 7, 1)),
            TierRow("pilot", "curated", None, Decimal("1"), _at(2020, 1, 1)),
        ),
    )
    before = tiers_at(config, "pilot", "individual", _at(2026, 6, 30))
    assert [(t.up_to_executions, t.markup_pct) for t in before] == [
        (2000, Decimal("75")),
        (10000, Decimal("70")),
        (None, Decimal("65")),
    ]
    after = tiers_at(config, "pilot", "individual", _at(2026, 7, 1))
    assert [t.markup_pct for t in after] == [Decimal("64"), Decimal("62"), Decimal("60")]
    assert tiers_at(config, "collaborator", "individual", _at(2026, 7, 1)) == ()


# ---------------------------------------------------------------------------------------------
# The shadow rule
# ---------------------------------------------------------------------------------------------

NOW = datetime(2026, 11, 12, 10, 30, tzinfo=UTC)  # 18:30 on 12 Nov in Kuala Lumpur


def test_back_dating_before_the_earliest_row_is_accepted() -> None:
    existing = [_at(2026, 3, 1), _at(2026, 9, 1)]
    check_new_price_date(existing, _at(2026, 2, 28), NOW)
    check_new_price_date(existing, _at(2019, 1, 1), NOW)


def test_a_date_inside_or_at_an_existing_priced_range_is_refused() -> None:
    existing = [_at(2026, 3, 1), _at(2026, 9, 1)]
    for inside in (_at(2026, 3, 1), _at(2026, 5, 1), _at(2026, 9, 1), _at(2026, 11, 1)):
        with pytest.raises(ShadowRuleError):
            check_new_price_date(existing, inside, NOW)


def test_today_and_tomorrow_are_accepted_three_days_ahead_is_refused() -> None:
    existing = [_at(2020, 1, 1)]
    check_new_price_date(existing, NOW, NOW)
    check_new_price_date(existing, _at(2026, 11, 12), NOW)  # today, 00:00
    check_new_price_date(existing, NOW + timedelta(days=1), NOW)  # tomorrow
    with pytest.raises(ShadowRuleError):
        check_new_price_date(existing, NOW + timedelta(days=3), NOW)
    with pytest.raises(ShadowRuleError):
        check_new_price_date(existing, NOW + timedelta(days=2), NOW)


def test_yesterday_is_refused_when_a_row_is_already_in_force() -> None:
    with pytest.raises(ShadowRuleError):
        check_new_price_date([_at(2020, 1, 1)], _at(2026, 11, 11), NOW)


def test_the_first_row_for_a_model_may_be_back_dated_but_never_far_ahead() -> None:
    # Nothing priced yet: back-dating fills the gap of unpriced calls.
    check_new_price_date([], _at(2026, 1, 1), NOW)
    check_new_price_date([], NOW, NOW)
    with pytest.raises(ShadowRuleError):
        check_new_price_date([], NOW + timedelta(days=3), NOW)


def test_today_is_the_kuala_lumpur_day() -> None:
    # 17:00 UTC on the 12th is 01:00 on the 13th in Kuala Lumpur, so the 12th is yesterday there.
    late = datetime(2026, 11, 12, 17, 0, tzinfo=UTC)
    existing = [_at(2020, 1, 1)]
    with pytest.raises(ShadowRuleError):
        check_new_price_date(existing, _at(2026, 11, 12), late)
    check_new_price_date(existing, _at(2026, 11, 13), late)
    check_new_price_date(existing, _at(2026, 11, 14), late)  # tomorrow in Kuala Lumpur
    with pytest.raises(ShadowRuleError):
        check_new_price_date(existing, _at(2026, 11, 15), late)


def test_naive_datetimes_are_refused_loudly() -> None:
    with pytest.raises(ValueError, match="timezone"):
        check_new_price_date([], datetime(2026, 11, 12), NOW)
    with pytest.raises(ValueError, match="timezone"):
        check_new_price_date([datetime(2020, 1, 1)], NOW, NOW)


# ---------------------------------------------------------------------------------------------
# Migration 0571
# ---------------------------------------------------------------------------------------------


@requires_docker
def test_tables_exist_with_the_agreed_columns_and_no_client_column(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        for table in TABLES:
            cols = {
                row["column_name"]: (row["data_type"], row["is_nullable"])
                for row in conn.execute(
                    "SELECT column_name, data_type, is_nullable FROM information_schema.columns"
                    " WHERE table_schema = 'public' AND table_name = %s",
                    (table,),
                )
            }
            assert cols, table
            assert "client_id" not in cols, table
            assert cols["effective_from"] == ("timestamp with time zone", "NO"), table
        prices = conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_name = 'ai_model_prices'"
        ).fetchall()
        types = {r["column_name"]: r["data_type"] for r in prices}
        assert types["input_usd_per_mtok"] == "numeric"
        assert types["output_usd_per_mtok"] == "numeric"
        [tier_limit] = conn.execute(
            "SELECT is_nullable, data_type FROM information_schema.columns"
            " WHERE table_name = 'billing_markup_tiers' AND column_name = 'up_to_executions'"
        )
        assert tier_limit == {"is_nullable": "YES", "data_type": "integer"}


@requires_docker
def test_fee_seeds_are_exact(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT stage, monthly_fee_rm, effective_from FROM billing_stage_fees"
        ).fetchall()
    assert {(r["stage"], r["monthly_fee_rm"]) for r in rows} == {
        ("pilot", Decimal("99.99")),
        ("collaborator", Decimal("119.99")),
        ("terminated", Decimal("0")),
    }
    assert {r["effective_from"] for r in rows} == {SEED_DATE}
    assert len(rows) == 3


@requires_docker
def test_markup_seeds_are_exact(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT stage, bundle, up_to_executions, markup_pct, effective_from"
            " FROM billing_markup_tiers"
        ).fetchall()
    assert len(rows) == 18
    assert {r["effective_from"] for r in rows} == {SEED_DATE}
    got = {(r["stage"], r["bundle"], r["up_to_executions"]): r["markup_pct"] for r in rows}
    want = {
        (stage, bundle, limit): Decimal(pct)
        for (stage, bundle), pcts in MARKUPS.items()
        for limit, pct in zip(TIER_LIMITS, pcts, strict=True)
    }
    assert got == want
    assert not [r for r in rows if r["stage"] == "terminated"]


@requires_docker
def test_price_and_fx_seeds_cover_the_default_models(migrated_dsn: str) -> None:
    groq = Settings.model_fields["GROQ_MODEL"].default
    gemini = Settings.model_fields["GOOGLE_AI_MODEL"].default
    assert (groq, gemini) == ("openai/gpt-oss-120b", "gemini-3.1-flash-lite")
    with _owner(migrated_dsn) as conn:
        prices = {
            (r["provider"], r["model"]): (
                r["input_usd_per_mtok"],
                r["output_usd_per_mtok"],
                r["effective_from"],
            )
            for r in conn.execute("SELECT * FROM ai_model_prices")
        }
        fx = conn.execute("SELECT usd_to_myr, effective_from FROM billing_fx_rates").fetchall()
    assert prices == {
        ("groq", groq): (Decimal("0.15"), Decimal("0.60"), SEED_DATE),
        ("gemini", gemini): (Decimal("0.25"), Decimal("1.50"), SEED_DATE),
    }
    assert fx == [{"usd_to_myr": Decimal("4.0840"), "effective_from": SEED_DATE}]


@requires_docker
def test_every_seeded_value_is_cited_in_the_migration() -> None:
    text = (ROOT / "db" / "migrations" / "0571_billing_config.sql").read_text("utf-8")
    assert "console.groq.com" in text
    assert "aistudio.google.com" in text
    assert "bnm.gov.my" in text
    assert "2026-11-12" in text


@requires_docker
def test_duplicates_are_refused(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        with pytest.raises(errors.UniqueViolation):
            conn.execute(
                "INSERT INTO ai_model_prices (provider, model, input_usd_per_mtok,"
                " output_usd_per_mtok, effective_from) VALUES"
                " ('groq', 'openai/gpt-oss-120b', 1, 1, %s)",
                (SEED_DATE,),
            )
        with pytest.raises(errors.UniqueViolation):
            conn.execute(
                "INSERT INTO billing_fx_rates (usd_to_myr, effective_from) VALUES (4, %s)",
                (SEED_DATE,),
            )
        with pytest.raises(errors.UniqueViolation):
            conn.execute(
                "INSERT INTO billing_stage_fees (stage, monthly_fee_rm, effective_from)"
                " VALUES ('pilot', 1, %s)",
                (SEED_DATE,),
            )
        with pytest.raises(errors.UniqueViolation):
            conn.execute(
                "INSERT INTO billing_markup_tiers (stage, bundle, up_to_executions, markup_pct,"
                " effective_from) VALUES ('pilot', 'individual', 2000, 1, %s)",
                (SEED_DATE,),
            )
        # NULL means open-ended: two open-ended tiers on the same date are still a duplicate.
        with pytest.raises(errors.UniqueViolation):
            conn.execute(
                "INSERT INTO billing_markup_tiers (stage, bundle, up_to_executions, markup_pct,"
                " effective_from) VALUES ('pilot', 'individual', NULL, 1, %s)",
                (SEED_DATE,),
            )


@requires_docker
def test_checks_refuse_bad_values(migrated_dsn: str) -> None:
    day = _at(2030, 1, 1)
    bad: list[tuple[str, tuple[Any, ...]]] = [
        (
            "INSERT INTO ai_model_prices (provider, model, input_usd_per_mtok,"
            " output_usd_per_mtok, effective_from) VALUES ('groq', 'x', -1, 1, %s)",
            (day,),
        ),
        (
            "INSERT INTO ai_model_prices (provider, model, input_usd_per_mtok,"
            " output_usd_per_mtok, effective_from) VALUES ('groq', 'x', 1, -1, %s)",
            (day,),
        ),
        ("INSERT INTO billing_fx_rates (usd_to_myr, effective_from) VALUES (0, %s)", (day,)),
        ("INSERT INTO billing_fx_rates (usd_to_myr, effective_from) VALUES (-4, %s)", (day,)),
        (
            "INSERT INTO billing_stage_fees (stage, monthly_fee_rm, effective_from)"
            " VALUES ('nonsense', 1, %s)",
            (day,),
        ),
        (
            "INSERT INTO billing_stage_fees (stage, monthly_fee_rm, effective_from)"
            " VALUES ('pilot', -1, %s)",
            (day,),
        ),
        (
            "INSERT INTO billing_markup_tiers (stage, bundle, up_to_executions, markup_pct,"
            " effective_from) VALUES ('pilot', 'nonsense', 10, 1, %s)",
            (day,),
        ),
        (
            "INSERT INTO billing_markup_tiers (stage, bundle, up_to_executions, markup_pct,"
            " effective_from) VALUES ('nonsense', 'curated', 10, 1, %s)",
            (day,),
        ),
        (
            "INSERT INTO billing_markup_tiers (stage, bundle, up_to_executions, markup_pct,"
            " effective_from) VALUES ('pilot', 'curated', 10, -1, %s)",
            (day,),
        ),
        (
            "INSERT INTO billing_markup_tiers (stage, bundle, up_to_executions, markup_pct,"
            " effective_from) VALUES ('pilot', 'curated', 0, 1, %s)",
            (day,),
        ),
    ]
    with _owner(migrated_dsn) as conn:
        for statement, params in bad:
            with pytest.raises(errors.CheckViolation):
                conn.execute(_sql(statement), params)


@requires_docker
@pytest.mark.parametrize("table", TABLES)
def test_app_role_can_select_and_insert_but_not_update_or_delete(
    migrated_dsn: str, app_dsn: str, table: str
) -> None:
    with DictConnection.connect(app_dsn, row_factory=dict_row) as conn:
        assert conn.execute(_sql(f"SELECT count(*) AS n FROM {table}")).fetchone()["n"] > 0  # type: ignore[index]
        for statement in (
            f"UPDATE {table} SET effective_from = effective_from",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with pytest.raises(errors.InsufficientPrivilege):
                conn.execute(_sql(statement))
            conn.rollback()
    with _owner(migrated_dsn) as conn:
        [grants] = conn.execute(
            "SELECT has_table_privilege('csa_app', %s, 'INSERT') AS ins,"
            " has_table_privilege('csa_app', %s, 'SELECT') AS sel,"
            " has_table_privilege('csa_identity', %s, 'SELECT') AS identity,"
            " has_table_privilege('public', %s, 'SELECT') AS pub",
            (f"public.{table}",) * 4,
        )
    assert grants == {"ins": True, "sel": True, "identity": False, "pub": False}


@requires_docker
def test_app_role_can_insert_new_rows(app_dsn: str) -> None:
    model = f"synthetic-{uuid.uuid4()}"
    with DictConnection.connect(app_dsn, row_factory=dict_row) as conn:
        conn.execute(
            "INSERT INTO ai_model_prices (provider, model, input_usd_per_mtok,"
            " output_usd_per_mtok, effective_from) VALUES ('groq', %s, 1, 2, %s)",
            (model, SEED_DATE),
        )
        conn.rollback()  # leave the shared table as migrated


@requires_docker
@pytest.mark.parametrize("table", TABLES)
def test_even_the_owner_cannot_edit_or_delete_history(migrated_dsn: str, table: str) -> None:
    with _owner(migrated_dsn) as conn:
        for statement in (
            f"UPDATE {table} SET effective_from = effective_from",
            f"DELETE FROM {table}",
        ):
            with pytest.raises(errors.RestrictViolation, match="append-only"):
                conn.execute(_sql(statement))
        # TRUNCATE stays open to the owner so test resets and staging resets keep working.
        with conn.transaction():
            conn.execute(_sql(f"TRUNCATE {table}"))
            assert conn.execute(_sql(f"SELECT count(*) AS n FROM {table}")).fetchone() == {"n": 0}
            raise psycopg.Rollback
        assert conn.execute(_sql(f"SELECT count(*) AS n FROM {table}")).fetchone()["n"] > 0  # type: ignore[index]


@requires_docker
async def test_load_config_reads_the_tables_as_decimals(app_dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(app_dsn) as conn:
        config = await load_config(conn)
    assert isinstance(config, BillingConfig)
    assert len(config.fees) == 3 and len(config.tiers) == 18
    assert len(config.fx) == 1 and len(config.prices) == 2
    assert all(isinstance(f.monthly_fee_rm, Decimal) for f in config.fees)
    assert all(isinstance(t.markup_pct, Decimal) for t in config.tiers)
    assert all(isinstance(p.input_usd_per_mtok, Decimal) for p in config.prices)
    assert fee_for(config, "pilot", NOW) == Decimal("99.99")
    assert fee_for(config, "collaborator", NOW) == Decimal("119.99")
    assert fee_for(config, "terminated", NOW) == Decimal("0")
    assert fx_at(config, NOW) == Decimal("4.0840")
    groq = price_at(config, "groq", "openai/gpt-oss-120b", _at(2026, 11, 1))
    assert groq is not None and groq.output_usd_per_mtok == Decimal("0.60")
    gemini = price_at(config, "gemini", "gemini-3.1-flash-lite", _at(2026, 11, 1))
    assert gemini is not None and gemini.input_usd_per_mtok == Decimal("0.25")
    tiers = tiers_at(config, "collaborator", "complete", NOW)
    assert [(t.up_to_executions, t.markup_pct) for t in tiers] == [
        (2000, Decimal("77.5")),
        (10000, Decimal("77.5")),
        (None, Decimal("77.5")),
    ]
    assert price_at(config, "groq", "no-such-model", NOW) is None


@requires_docker
async def test_price_at_picks_the_row_in_force_at_occurred_at_from_the_database(
    migrated_dsn: str,
) -> None:
    model = f"synthetic-{uuid.uuid4()}"
    async with await psycopg.AsyncConnection.connect(migrated_dsn) as owner:
        await owner.execute(
            "INSERT INTO ai_model_prices (provider, model, input_usd_per_mtok,"
            " output_usd_per_mtok, effective_from) VALUES"
            " ('groq', %s, 1, 2, %s), ('groq', %s, 5, 6, %s)",
            (model, _at(2026, 1, 1), model, _at(2026, 6, 1)),
        )
        config = await load_config(owner)
        await owner.rollback()  # the table is append-only, so leave it as migrated
    early = price_at(config, "groq", model, _at(2026, 3, 1))
    late = price_at(config, "groq", model, _at(2026, 7, 1))
    assert early is not None and early.input_usd_per_mtok == Decimal("1")
    assert late is not None and late.input_usd_per_mtok == Decimal("5")
    assert price_at(config, "groq", model, _at(2025, 1, 1)) is None


@requires_docker
def test_migration_is_recorded_and_the_expected_version_has_reached_it(migrated_dsn: str) -> None:
    with _owner(migrated_dsn) as conn:
        [row] = conn.execute(
            "SELECT count(*) AS n FROM schema_migrations WHERE version = %s", (MIGRATION,)
        )
    assert row["n"] == 1
    assert EXPECTED_SCHEMA_VERSION >= MIGRATION


def test_the_insert_sql_files_exist() -> None:
    for name in ("prices_insert.sql", "fx_insert.sql"):
        text = (ROOT / "db" / "sql" / "billing" / name).read_text("utf-8")
        assert "INSERT INTO" in text
