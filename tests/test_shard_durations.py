"""Turning pytest junit timings into a per-file seconds file for the CI shard selector."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.shard_durations import main, merge, read_junit, report, write_durations


def junit(tmp_path: Path, cases: str, name: str = "junit.xml") -> Path:
    path = tmp_path / name
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        f"{cases}</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def test_read_junit_sums_the_cases_of_one_file_and_keeps_files_separate(tmp_path: Path) -> None:
    path = junit(
        tmp_path,
        '<testcase classname="tests.test_a" name="one" time="1.5" file="tests/test_a.py"/>'
        '<testcase classname="tests.test_a" name="two" time="2.25" file="tests/test_a.py"/>'
        '<testcase classname="tests.test_b" name="one" time="0.5" file="tests/test_b.py"/>',
    )
    assert read_junit(path) == {"tests/test_a.py": 3.75, "tests/test_b.py": 0.5}


def test_a_skipped_case_counts_zero(tmp_path: Path) -> None:
    path = junit(
        tmp_path,
        '<testcase classname="tests.test_a" name="one" time="2.0"/>'
        '<testcase classname="tests.test_a" name="two" time="9.0">'
        '<skipped message="x"/></testcase>',
    )
    assert read_junit(path) == {"tests/test_a.py": 2.0}


def test_a_file_with_only_skipped_cases_is_kept_at_zero(tmp_path: Path) -> None:
    path = junit(
        tmp_path,
        '<testcase classname="tests.test_a" name="one" time="0.0">'
        '<skipped message="x"/></testcase>',
    )
    assert read_junit(path) == {"tests/test_a.py": 0.0}


def test_a_nested_classname_resolves_to_the_module_file(tmp_path: Path) -> None:
    path = junit(tmp_path, '<testcase classname="tests.test_x.TestY" name="one" time="1.0"/>')
    assert read_junit(path) == {"tests/test_x.py": 1.0}


def test_an_unresolvable_classname_is_ignored_with_a_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = junit(
        tmp_path,
        '<testcase classname="other.module" name="one" time="1.0"/>'
        '<testcase classname="" name="two" time="1.0"/>'
        '<testcase name="three" time="1.0"/>'
        '<testcase classname="tests.test_a" name="four" time="bogus"/>'
        '<testcase classname="tests.test_a" name="six" time="nan"/>'
        '<testcase classname="tests.test_a" name="seven" time="inf"/>'
        '<testcase classname="tests.test_a" name="five" time="0.3"/>',
    )
    assert read_junit(path) == {"tests/test_a.py": 0.3}
    assert "warning" in capsys.readouterr().err


def test_a_tiny_or_zero_time_does_not_break_anything(tmp_path: Path) -> None:
    path = junit(
        tmp_path,
        '<testcase classname="tests.test_a" name="one" time="0"/>'
        '<testcase classname="tests.test_b" name="one" time="0.0004"/>',
    )
    durations = read_junit(path)
    assert durations == {"tests/test_a.py": 0.0, "tests/test_b.py": 0.0004}
    assert "floor:" in report(durations, 2)


def test_the_same_file_in_two_junit_inputs_is_summed(tmp_path: Path) -> None:
    case = '<testcase classname="tests.test_a" name="x" time="1.5" file="tests/test_a.py"/>'
    first = read_junit(junit(tmp_path, case, "one.xml"))
    second = read_junit(junit(tmp_path, case, "two.xml"))
    assert merge(first, second) == {"tests/test_a.py": 3.0}


def test_a_file_attribute_outside_tests_falls_back_to_the_classname(tmp_path: Path) -> None:
    path = junit(
        tmp_path,
        '<testcase classname="tests.test_a" name="x" time="1.0" file="elsewhere/conftest.py"/>',
    )
    assert read_junit(path) == {"tests/test_a.py": 1.0}


def test_merge_sums_a_file_present_in_both_parts() -> None:
    merged = merge({"tests/test_a.py": 1.0, "tests/test_b.py": 2.0}, {"tests/test_a.py": 0.5})
    assert merged == {"tests/test_a.py": 1.5, "tests/test_b.py": 2.0}


def test_write_durations_is_sorted_one_decimal_and_drops_foreign_keys(tmp_path: Path) -> None:
    out = tmp_path / "durations.json"
    write_durations(
        {
            "tests/test_b.py": 2.26,
            "tests/test_a.py": 0.04,
            "other/x.py": 5.0,
            "tests/notes.txt": 1.0,
        },
        out,
    )
    text = out.read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert json.loads(text) == {"tests/test_a.py": 0.0, "tests/test_b.py": 2.3}
    assert list(json.loads(text)) == ["tests/test_a.py", "tests/test_b.py"]
    assert text.index("test_a") < text.index("test_b")


def test_report_lists_every_shard_and_the_floor() -> None:
    durations = {"tests/test_a.py": 10.0, "tests/test_b.py": 6.0, "tests/test_c.py": 4.0}
    text = report(durations, 2)
    lines = text.splitlines()
    shard_lines = [line for line in lines if line.startswith("shard ")]
    assert len(shard_lines) == 2
    totals = [float(line.split()[2]) for line in shard_lines]
    assert sum(totals) == pytest.approx(20.0, abs=0.1)
    assert "floor: 10.0" in lines
    assert "ideal: 10.0" in text
    assert "tests/test_a.py" in text


def test_the_cli_merges_two_junit_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    one = junit(tmp_path, '<testcase classname="tests.test_a" name="x" time="1.0"/>', "one.xml")
    two = junit(
        tmp_path,
        '<testcase classname="tests.test_a" name="y" time="2.0"/>'
        '<testcase classname="tests.test_b" name="z" time="0.5"/>',
        "two.xml",
    )
    out = tmp_path / "out.json"
    assert main(["merge", str(one), str(two), "--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "tests/test_a.py": 3.0,
        "tests/test_b.py": 0.5,
    }


def test_the_cli_exits_2_naming_a_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.xml"
    out = tmp_path / "out.json"
    assert main(["merge", str(missing), "--out", str(out)]) == 2
    assert "nope.xml" in capsys.readouterr().err
    assert not out.exists()


def test_the_cli_exits_2_naming_an_unparsable_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuites><oops", encoding="utf-8")
    assert main(["merge", str(broken), "--out", str(tmp_path / "out.json")]) == 2
    assert "broken.xml" in capsys.readouterr().err


def test_the_cli_report_reads_the_durations_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    durations = tmp_path / "d.json"
    durations.write_text(json.dumps({"tests/test_a.py": 5.0, "tests/test_b.py": 3.0}), "utf-8")
    assert main(["report", "--counts", "2,3", "--durations", str(durations)]) == 0
    printed = capsys.readouterr().out
    assert printed.count("floor:") == 2
    assert main(["report", "--counts", "2", "--durations", str(tmp_path / "gone.json")]) == 2


def test_report_weighs_zero_second_files_as_ci_does() -> None:
    from tests.shard import shard_modules

    durations = {f"tests/test_{name}.py": 0.0 for name in "abcde"}
    expected = shard_modules(dict.fromkeys(durations, 1), 2)
    text = report(durations, 2)
    sizes = sorted(len([m for m, n in expected.items() if n == shard]) for shard in (1, 2))
    assert sizes == [2, 3]
    assert [f"({size} files)" in text for size in sizes] == [True, True]


def test_the_cli_report_exits_2_on_a_bad_durations_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for content in ('{"tests/test_a.py": -1}', '{"tests/test_a.py": null}', "[1, 2]"):
        bad = tmp_path / "bad.json"
        bad.write_text(content, encoding="utf-8")
        assert main(["report", "--counts", "2", "--durations", str(bad)]) == 2
        assert "bad.json" in capsys.readouterr().err


def test_the_cli_merge_exits_2_when_the_output_directory_is_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    one = junit(tmp_path, '<testcase classname="tests.test_a" name="x" time="1.0"/>')
    out = tmp_path / "no_such_dir" / "out.json"
    assert main(["merge", str(one), "--out", str(out)]) == 2
    assert "out.json" in capsys.readouterr().err
