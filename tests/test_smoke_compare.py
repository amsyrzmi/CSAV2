"""The gate that compares two assistant_smoke runs case by case."""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.smoke_compare import compare, main, parse_rates

BASE = {"a-1": (5, 5), "b-1": (3, 5), "c-1": (5, 5)}


def test_parse_rates_reads_only_rate_lines() -> None:
    text = "FAIL a-1 action=answer\nRATE a-1 4/5\nRATE b-1 5/5\n9 passed, 1 failed\n"
    assert parse_rates(text) == {"a-1": (4, 5), "b-1": (5, 5)}


def test_equal_runs_pass() -> None:
    assert compare(BASE, dict(BASE)) == []


def test_one_sample_drop_passes_when_the_total_does_not_fall() -> None:
    cand = {"a-1": (4, 5), "b-1": (4, 5), "c-1": (5, 5)}
    assert compare(BASE, cand) == []


def test_a_case_falling_two_or_more_is_a_violation() -> None:
    cand = {"a-1": (3, 5), "b-1": (5, 5), "c-1": (5, 5)}
    lines = compare(BASE, cand)
    assert any("a-1" in line and "5/5 to 3/5" in line for line in lines)


def test_a_lower_total_is_a_violation() -> None:
    cand = {"a-1": (4, 5), "b-1": (3, 5), "c-1": (5, 5)}
    assert any("total" in line for line in compare(BASE, cand))


def test_a_missing_case_is_a_violation_naming_it() -> None:
    cand = {"a-1": (5, 5), "b-1": (3, 5)}
    assert any("c-1" in line and "missing" in line for line in compare(BASE, cand))


def test_different_repeat_is_one_clear_violation() -> None:
    cand = {"a-1": (10, 10), "b-1": (6, 10), "c-1": (10, 10)}
    lines = compare(BASE, cand)
    assert len(lines) == 1 and "different --repeat" in lines[0]


def _files(tmp_path: Path, base: str, cand: str) -> list[str]:
    (tmp_path / "base.txt").write_text(base)
    (tmp_path / "cand.txt").write_text(cand)
    return [str(tmp_path / "base.txt"), str(tmp_path / "cand.txt")]


def test_main_exits_one_and_prints_the_violation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _files(tmp_path, "RATE a-1 5/5\n", "RATE a-1 2/5\n")
    assert main(argv) == 1
    assert "VIOLATION" in capsys.readouterr().out


def test_main_exits_zero_and_prints_totals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = _files(tmp_path, "RATE a-1 4/5\n", "RATE a-1 5/5\n")
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "a-1" in out and "total 4/5 to 5/5" in out and "VIOLATION" not in out
