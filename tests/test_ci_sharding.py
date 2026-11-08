"""The CI shard selector: every test file lands in exactly one shard, the same way every time."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.shard import SLOW_WEIGHT, parse_shard, select, shard_modules


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
