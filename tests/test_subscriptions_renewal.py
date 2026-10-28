"""Renewals keep the cycle's day: migration 0481, the next-renewal function and its backfill."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
import pytest
from apps.gateway.main import EXPECTED_SCHEMA_VERSION

from tests.conftest import owner_dsn_for, requires_docker

pytestmark = [requires_docker, pytest.mark.usefixtures("migrated_dsn")]

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "migrations"
    / "0481_subscription_renewal_anchor.sql"
)
KL = "Asia/Kuala_Lumpur"


def at(text: str) -> datetime:
    """A timestamp written in the Asia/Kuala_Lumpur calendar (UTC+8)."""
    return datetime.fromisoformat(f"{text}+08:00")


def next_renewal(
    conn: psycopg.Connection[Any],
    anchor: datetime | None,
    after: datetime | None,
    interval: str | None,
    tz: str | None = KL,
) -> datetime | None:
    row = conn.execute(
        "SELECT subscription_next_renewal(%s,%s,%s,%s)", (anchor, after, interval, tz)
    ).fetchone()
    assert row is not None
    return row[0]


def chain(
    conn: psycopg.Connection[Any], anchor: datetime, interval: str, steps: int
) -> list[datetime | None]:
    results: list[datetime | None] = []
    after = anchor
    for _ in range(steps):
        after = next_renewal(conn, anchor, after, interval)
        assert after is not None
        results.append(after)
    return results


@pytest.fixture
def owner(pg_container: object) -> Any:
    with psycopg.connect(owner_dsn_for(pg_container), autocommit=True) as conn:
        yield conn


def test_next_renewal_monthly_keeps_the_day(owner: psycopg.Connection[Any]) -> None:
    assert chain(owner, at("2026-10-31 10:00"), "month", 5) == [
        at("2026-11-30 10:00"),
        at("2026-12-31 10:00"),
        at("2027-01-31 10:00"),
        at("2027-02-28 10:00"),
        at("2027-03-31 10:00"),
    ]


def test_next_renewal_yearly_from_29_february(owner: psycopg.Connection[Any]) -> None:
    assert chain(owner, at("2028-02-29 09:00"), "year", 4) == [
        at("2029-02-28 09:00"),
        at("2030-02-28 09:00"),
        at("2031-02-28 09:00"),
        at("2032-02-29 09:00"),
    ]


def test_next_renewal_is_the_first_cycle_date_after(owner: psycopg.Connection[Any]) -> None:
    assert next_renewal(owner, at("2026-01-15 10:00"), at("2026-02-10 10:00"), "month") == at(
        "2026-02-15 10:00"
    )
    # the anchor is still ahead of `after`: n = 0
    assert next_renewal(owner, at("2026-03-15 10:00"), at("2026-03-01 10:00"), "month") == at(
        "2026-03-15 10:00"
    )
    # strictly after: an `after` on a cycle date moves one interval on
    assert next_renewal(owner, at("2026-03-15 10:00"), at("2026-03-15 10:00"), "month") == at(
        "2026-04-15 10:00"
    )
    assert next_renewal(owner, at("2026-03-15 10:00"), at("2026-03-15 10:00"), "year") == at(
        "2027-03-15 10:00"
    )
    # `after` earlier in the month than the cycle time, and later in the month
    assert next_renewal(owner, at("2026-01-15 10:00"), at("2026-05-15 09:59"), "month") == at(
        "2026-05-15 10:00"
    )
    assert next_renewal(owner, at("2026-01-15 10:00"), at("2026-05-15 10:01"), "month") == at(
        "2026-06-15 10:00"
    )
    for args in [
        (None, at("2026-01-01 00:00"), "month", KL),
        (at("2026-01-01 00:00"), None, "month", KL),
        (at("2026-01-01 00:00"), at("2026-01-01 00:00"), None, KL),
        (at("2026-01-01 00:00"), at("2026-01-01 00:00"), "month", None),
    ]:
        assert next_renewal(owner, *args) is None


def test_next_renewal_uses_the_tenant_calendar(owner: psycopg.Connection[Any]) -> None:
    anchor = at("2026-01-31 00:30")
    result = next_renewal(owner, anchor, anchor, "month")
    assert result == at("2026-02-28 00:30")
    # 2026-02-27 16:30Z; UTC arithmetic would give 2026-02-28 16:30Z
    assert result == datetime(2026, 2, 27, 16, 30, tzinfo=UTC)


def test_backfill_anchors_on_cycle_rows_only(owner: psycopg.Connection[Any]) -> None:
    client = uuid.uuid4()
    owner.execute(
        "INSERT INTO clients(id,slug,name,status,platform_version,timezone) "
        "VALUES (%s,%s,'Synthetic renewals','active','b1-test',%s)",
        (client, f"renewal-anchor-{client}", KL),
    )
    for code, interval in [("basic", "month"), ("annual", "year")]:
        owner.execute(
            "INSERT INTO plans(client_id,code,name,price,billing_interval) VALUES (%s,%s,%s,10,%s)",
            (client, code, code.title(), interval),
        )
    cases: dict[str, tuple[str, datetime, datetime | None, datetime | None]] = {
        "cycle": ("basic", at("2026-10-31 10:00"), at("2026-11-30 10:00"), None),
        "extended": ("basic", at("2026-10-31 10:00"), at("2026-12-15 10:00"), None),
        "no_renewal": ("basic", at("2026-10-31 10:00"), None, None),
        "annual": ("annual", at("2027-03-01 10:00"), at("2028-03-01 10:00"), None),
        "preset": ("basic", at("2026-10-31 10:00"), at("2026-11-30 10:00"), at("2026-05-05 10:00")),
        "ghost": ("ghost", at("2026-01-31 10:00"), at("2026-02-28 10:00"), None),
    }
    ids: dict[str, uuid.UUID] = {}
    for name, (plan, started, renews, anchor) in cases.items():
        ids[name] = uuid.uuid4()
        owner.execute(
            "INSERT INTO subscriptions"
            "(id,client_id,plan,status,price,started_at,renews_at,renewal_anchor) "
            "VALUES (%s,%s,%s,'active',10,%s,%s,%s)",
            (ids[name], client, plan, started, renews, anchor),
        )

    # The file is idempotent and never writes schema_migrations, so re-running it is safe.
    owner.execute(MIGRATION.read_text())
    owner.execute(MIGRATION.read_text())

    anchors = {
        name: owner.execute(
            "SELECT renewal_anchor FROM subscriptions WHERE id=%s", (sub,)
        ).fetchone()
        for name, sub in ids.items()
    }
    assert {name: row[0] if row else "missing" for name, row in anchors.items()} == {
        "cycle": cases["cycle"][1],
        "extended": None,
        "no_renewal": None,
        "annual": cases["annual"][1],
        "preset": at("2026-05-05 10:00"),
        "ghost": cases["ghost"][1],
    }


def test_0481_is_recorded_and_expected(owner: psycopg.Connection[Any]) -> None:
    assert EXPECTED_SCHEMA_VERSION == "0481"
    row = owner.execute("SELECT max(version) FROM schema_migrations").fetchone()
    assert row is not None and row[0] == EXPECTED_SCHEMA_VERSION
