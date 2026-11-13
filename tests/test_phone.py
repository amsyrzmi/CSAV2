"""normalise_my_phone and its SQL twin public.my_phone_digits (0581) agree on one table of cases."""

from __future__ import annotations

import pytest
from csa.core.phone import normalise_my_phone

from tests.conftest import requires_docker
from tests.test_bookings_schema import _owner

CASES: list[tuple[str, str | None]] = [
    ("012-345 6789", "+60123456789"),
    ("+60 12 345 6789", "+60123456789"),
    ("60123456789", "+60123456789"),
    ("+44 7911 123456", "+447911123456"),
    ("(03) 1234-5678", "+60312345678"),
    ("0123456", "+60123456"),
    # A leading 00 is not an international prefix here: the SQL turns it into 600..., and so do we.
    ("0044 20 7946 000", "+60044207946000"),
    ("0044 20 7946 0000", "+600442079460000"),
    ("12345678", "+12345678"),
    ("123456789012345", "+123456789012345"),
    ("abc", None),
    ("", None),
    ("123", None),
    ("1234567", None),
    ("1234567890123456", None),
    ("0044 20 7946 00000", None),
    # Only ASCII digits count, as in the SQL's [^0-9].
    ("٠١٢٣٤٥٦٧٨٩", None),
]


@pytest.mark.parametrize(("raw", "expected"), CASES)
def test_normalise_my_phone(raw: str, expected: str | None) -> None:
    assert normalise_my_phone(raw) == expected


@requires_docker
def test_sql_my_phone_digits_agrees_with_python(migrated_dsn: str) -> None:
    raws = [raw for raw, _ in CASES]
    with _owner(migrated_dsn) as conn:
        rows = conn.execute(
            "SELECT raw, public.my_phone_digits(raw) AS digits FROM unnest(%s::text[]) AS raw",
            (raws,),
        ).fetchall()
    assert [r["raw"] for r in rows] == raws
    for row in rows:
        python = normalise_my_phone(row["raw"])
        if python is None:
            assert not 8 <= len(row["digits"]) <= 15, row
        else:
            assert row["digits"] == python.removeprefix("+"), row
