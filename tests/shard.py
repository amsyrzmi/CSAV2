"""Split the suite across CI jobs by whole test file.

CI sets ``CSA_TEST_SHARD=<n>/<total>``; ``tests/conftest.py`` then keeps only this shard's files.
A file is never split, so module-scoped fixtures (one Postgres container per file) run once.
The split is deterministic, so every job agrees on who runs what. A file weighs the number of
its tests, and a test marked ``slow`` (image builds, packaged frontend) weighs ``SLOW_WEIGHT``
tests. Change the weights here to rebalance; nothing else needs to know.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

SLOW_WEIGHT = 20


def parse_shard(spec: str) -> tuple[int, int]:
    """``"2/4"`` becomes ``(2, 4)``; anything else is a configuration error, not a skipped shard."""
    try:
        index_text, count_text = spec.split("/")
        index, count = int(index_text), int(count_text)
    except ValueError:
        raise ValueError(f"CSA_TEST_SHARD must look like 'N/M', got {spec!r} (shard)") from None
    if not 1 <= index <= count:
        raise ValueError(f"CSA_TEST_SHARD index must be within 1..M, got {spec!r} (shard)")
    return index, count


def shard_modules(weights: dict[str, int], count: int) -> dict[str, int]:
    """Give each module a 1-based shard: heaviest first, each onto the lightest shard so far."""
    loads = [0] * count
    placed: dict[str, int] = {}
    for module in sorted(weights, key=lambda name: (-weights[name], name)):
        lightest = min(range(count), key=lambda shard: (loads[shard], shard))
        loads[lightest] += weights[module]
        placed[module] = lightest + 1
    return placed


def select(items: Sequence[Any], spec: str) -> tuple[list[Any], list[Any]]:
    """Split collected items into (this shard's, everyone else's)."""
    index, count = parse_shard(spec)
    weights: dict[str, int] = {}
    for item in items:
        module = item.path.as_posix()
        weights[module] = weights.get(module, 0) + (
            SLOW_WEIGHT if item.get_closest_marker("slow") else 1
        )
    placed = shard_modules(weights, count)
    kept = [item for item in items if placed[item.path.as_posix()] == index]
    dropped = [item for item in items if placed[item.path.as_posix()] != index]
    return kept, dropped
