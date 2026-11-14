"""The CI shard selector: every test file lands in exactly one shard, the same way every time."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests import shard as shard_module
from tests.shard import (
    SLOW_WEIGHT,
    file_weights,
    load_durations,
    parse_shard,
    select,
    shard_modules,
)


class Item:
    """Just enough of a pytest item for the selector: a path and an optional slow marker."""

    def __init__(self, path: str, slow: bool = False) -> None:
        self.path = Path(path)
        self.slow = slow

    def get_closest_marker(self, name: str) -> object | None:
        return object() if (name == "slow" and self.slow) else None


@pytest.mark.parametrize("spec", ["1/4", "4/4", "1/1"])
def test_a_valid_shard_spec_parses(spec: str) -> None:
    index, count = spec.split("/")
    assert parse_shard(spec) == (int(index), int(count))


@pytest.mark.parametrize("spec", ["", "0/4", "5/4", "1/0", "a/b", "1", "1/2/3", "-1/4"])
def test_a_malformed_shard_spec_is_refused(spec: str) -> None:
    with pytest.raises(ValueError, match="shard"):
        parse_shard(spec)


def test_every_module_gets_exactly_one_shard_and_loads_are_close() -> None:
    weights = {
        f"tests/test_{i}.py": w for i, w in enumerate([90, 70, 60, 40, 40, 30, 20, 10, 5, 1])
    }
    placed = shard_modules(weights, 4)
    assert set(placed) == set(weights)
    assert set(placed.values()) <= {1, 2, 3, 4}
    loads = [sum(w for m, w in weights.items() if placed[m] == s) for s in (1, 2, 3, 4)]
    assert max(loads) - min(loads) <= max(weights.values())


def test_the_assignment_does_not_depend_on_input_order() -> None:
    weights = {f"tests/test_{i}.py": (i * 7) % 13 + 1 for i in range(40)}
    reversed_order = dict(reversed(list(weights.items())))
    assert shard_modules(weights, 5) == shard_modules(reversed_order, 5)


def test_select_keeps_whole_files_and_every_test_runs_in_exactly_one_shard() -> None:
    items = [Item(f"tests/test_{i % 7}.py") for i in range(60)] + [Item("tests/test_slow.py", True)]
    kept_by_shard = []
    for shard in (1, 2, 3):
        kept, dropped = select(items, f"{shard}/3")
        assert len(kept) + len(dropped) == len(items)
        kept_by_shard.append(kept)
    ids = [id(item) for kept in kept_by_shard for item in kept]
    assert sorted(ids) == sorted(id(item) for item in items)
    for kept in kept_by_shard:
        files = {item.path for item in kept}
        assert all(item in kept for item in items if item.path in files)


def test_a_slow_test_weighs_more_than_a_plain_one() -> None:
    items = [Item("tests/test_a.py", True)] + [
        Item("tests/test_b.py") for _ in range(SLOW_WEIGHT - 1)
    ]
    placed = shard_modules({"tests/test_a.py": SLOW_WEIGHT, "tests/test_b.py": SLOW_WEIGHT - 1}, 2)
    assert placed["tests/test_a.py"] != placed["tests/test_b.py"]
    kept_one, _ = select(items, "1/2")
    kept_two, _ = select(items, "2/2")
    assert {i.path.name for i in kept_one} != {i.path.name for i in kept_two}


def _example_items() -> list[Item]:
    return [Item(f"tests/test_{i % 7}.py") for i in range(60)] + [Item("tests/test_slow.py", True)]


def test_an_empty_durations_file_assigns_exactly_as_the_count_method() -> None:
    items = _example_items()
    counts: dict[str, int] = {}
    for item in items:
        key = item.path.as_posix()
        counts[key] = counts.get(key, 0) + (SLOW_WEIGHT if item.slow else 1)
    for total in (2, 3, 4, 6):
        placed = shard_modules(counts, total)
        for number in range(1, total + 1):
            kept, _ = select(items, f"{number}/{total}", {})
            assert {i.path.as_posix() for i in kept} == {
                m for m, s in placed.items() if s == number
            }


def test_the_default_durations_come_from_the_committed_file() -> None:
    items = _example_items()
    assert select(items, "1/3") == select(items, "1/3", load_durations())


def test_a_long_file_with_few_tests_is_balanced_by_seconds_not_by_count() -> None:
    items = [Item("tests/test_long.py")] + [Item(f"tests/test_s{i}.py") for i in range(20)]
    durations = {"tests/test_long.py": 600.0} | {f"tests/test_s{i}.py": 60.0 for i in range(20)}
    loads = []
    for number in (1, 2):
        kept, _ = select(items, f"{number}/2", durations)
        loads.append(sum(durations[i.path.as_posix()] for i in kept))
    assert abs(loads[0] - loads[1]) <= 600.0
    assert loads == [900.0, 900.0]
    # By count alone the one-test file is the lightest and the shards end up 11 / 10 files.
    by_count = shard_modules({i.path.as_posix(): 1 for i in items}, 2)
    assert sorted(list(by_count.values()).count(n) for n in (1, 2)) == [10, 11]


def test_a_new_file_gets_a_fallback_scaled_from_the_recorded_files() -> None:
    counts = {"tests/test_a.py": 10, "tests/test_new.py": 10}
    weights = file_weights(counts, {"tests/test_a.py": 100.0})
    assert weights == {"tests/test_a.py": 100000, "tests/test_new.py": 100000}
    small = file_weights(counts, {"tests/test_a.py": 0.5})
    assert small["tests/test_new.py"] == 500
    scaled = file_weights(
        {"tests/test_a.py": 10, "tests/test_new.py": 3}, {"tests/test_a.py": 20.0}
    )
    assert scaled["tests/test_new.py"] == 6000


def test_with_no_recorded_entry_the_count_weighs_a_thousand_milliseconds_per_test() -> None:
    counts = {"tests/test_a.py": 3, "tests/test_b.py": 20}
    assert file_weights(counts, {}) == {"tests/test_a.py": 3000, "tests/test_b.py": 20000}
    assert file_weights(counts, {"tests/test_elsewhere.py": 50.0}) == file_weights(counts, {})


def test_a_zero_or_tiny_duration_weighs_at_least_one_and_never_zeroes_the_fallback() -> None:
    counts = {"tests/test_a.py": 4, "tests/test_b.py": 5, "tests/test_new.py": 7}
    for seconds in (0.0, 0.0001):
        weights = file_weights(counts, {"tests/test_a.py": seconds, "tests/test_b.py": seconds})
        assert weights["tests/test_a.py"] == 1
        assert weights["tests/test_b.py"] == 1
        assert weights["tests/test_new.py"] >= 1
    assert (
        file_weights({"tests/test_new.py": 1}, {"tests/test_a.py": 0.0})["tests/test_new.py"] >= 1
    )


def test_the_key_order_of_the_durations_does_not_change_the_assignment() -> None:
    items = [Item(f"tests/test_{i % 11}.py") for i in range(80)] + [Item("tests/test_new.py")]
    durations = {f"tests/test_{i}.py": (i * 37) % 17 + 0.3 for i in range(11)}
    shuffled = dict(reversed(list(durations.items())))
    assert list(durations) != list(shuffled)
    for number in range(1, 7):
        assert select(items, f"{number}/6", durations) == select(items, f"{number}/6", shuffled)
    counts = {"tests/test_a.py": 3, "tests/test_b.py": 7, "tests/test_new.py": 5}
    forward = {"tests/test_a.py": 1.23, "tests/test_b.py": 4.56}
    assert file_weights(counts, forward) == file_weights(
        counts, dict(reversed(list(forward.items())))
    )


@pytest.mark.parametrize(
    "content",
    ["not json", "[1, 2]", '{"tests/test_a.py": -1}', '{"tests/test_a.py": "12"}'],
)
def test_a_malformed_durations_file_fails_loudly_naming_the_file(
    tmp_path: Path, content: str
) -> None:
    path = tmp_path / "durations_under_test.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=r"durations_under_test\.json") as raised:
        load_durations(path)
    assert "shard" in str(raised.value)


@pytest.mark.parametrize("content", ['{"tests/test_a.py": NaN}', '{"tests/test_a.py": true}'])
def test_a_non_finite_or_boolean_duration_is_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "d.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="shard"):
        load_durations(path)


def test_a_missing_durations_file_is_empty_and_a_valid_one_loads(tmp_path: Path) -> None:
    assert load_durations(tmp_path / "gone.json") == {}
    path = tmp_path / "d.json"
    path.write_text(json.dumps({"tests/test_a.py": 1.5, "tests/test_b.py": 0}), encoding="utf-8")
    assert load_durations(path) == {"tests/test_a.py": 1.5, "tests/test_b.py": 0.0}


def test_select_reads_the_committed_durations_file_and_passes_a_malformed_one_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = tmp_path / "shard_durations.json"
    broken.write_text("{", encoding="utf-8")
    monkeypatch.setattr(shard_module, "DURATIONS_FILE", broken)
    with pytest.raises(ValueError, match=r"shard_durations\.json"):
        select(_example_items(), "1/2")
    with pytest.raises(ValueError, match=r"shard_durations\.json"):
        load_durations()


def test_select_computes_each_items_key_once(monkeypatch: pytest.MonkeyPatch) -> None:
    items = _example_items()
    calls: list[Path] = []
    real = shard_module._module_key

    def counting(path: Path) -> str:
        calls.append(path)
        return real(path)

    monkeypatch.setattr(shard_module, "_module_key", counting)
    kept, dropped = select(items, "1/3", {})
    assert len(calls) == len(items)
    assert len(kept) + len(dropped) == len(items)


def test_every_test_is_kept_by_exactly_one_shard_with_partial_durations() -> None:
    items = [Item(f"tests/test_{i % 9}.py") for i in range(70)] + [Item("tests/test_slow.py", True)]
    durations = {"tests/test_1.py": 40.0, "tests/test_4.py": 0.0, "tests/test_8.py": 12.5}
    kept_ids: list[int] = []
    for number in range(1, 7):
        kept, dropped = select(items, f"{number}/6", durations)
        assert len(kept) + len(dropped) == len(items)
        kept_ids.extend(id(item) for item in kept)
    assert sorted(kept_ids) == sorted(id(item) for item in items)


def test_the_committed_durations_file_loads() -> None:
    assert isinstance(load_durations(shard_module.DURATIONS_FILE), dict)
    assert shard_module.DURATIONS_FILE.name == "shard_durations.json"


def test_an_absolute_pytest_path_matches_a_repo_relative_duration_key() -> None:
    absolute = Item(str(shard_module.ROOT / "tests" / "test_a.py"))
    others = [Item(f"tests/test_other{i}.py") for i in range(4)]
    durations = {"tests/test_a.py": 1000.0} | {f"tests/test_other{i}.py": 1.0 for i in range(4)}
    shards = [select([absolute, *others], f"{n}/2", durations)[0] for n in (1, 2)]
    assert sorted(len(kept) for kept in shards) == [1, 4]
    assert [i.path.name for kept in shards for i in kept if len(kept) == 1] == ["test_a.py"]


@pytest.mark.parametrize("total", [4, 6])
def test_the_real_test_files_each_land_on_exactly_one_shard_with_empty_durations(
    total: int,
) -> None:
    """The real tests/ directory (counts read from the source), split with no durations.

    Items carry absolute paths as pytest gives them; each shard must equal the old count method.
    """
    items: list[Item] = []
    counts: dict[str, int] = {}
    for path in sorted(Path(__file__).parent.glob("test_*.py")):
        count = max(1, len(re.findall(r"^\s*(?:async )?def test_", path.read_text("utf-8"), re.M)))
        absolute = shard_module.ROOT / "tests" / path.name
        items.extend(Item(str(absolute)) for _ in range(count))
        counts[absolute.as_posix()] = count
    assert len(counts) > 100
    placed = shard_modules(counts, total)
    owners: dict[str, list[int]] = {file: [] for file in counts}
    for number in range(1, total + 1):
        kept, dropped = select(items, f"{number}/{total}", {})
        assert len(kept) + len(dropped) == len(items)
        files = {item.path.as_posix() for item in kept}
        assert files == {file for file, shard in placed.items() if shard == number}
        for file in files:
            owners[file].append(number)
    assert all(len(numbers) == 1 for numbers in owners.values())
