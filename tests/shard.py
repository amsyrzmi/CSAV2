r"""Split the suite across CI jobs by whole test file.

CI sets ``CSA_TEST_SHARD=<n>/<total>``; ``tests/conftest.py`` then keeps only this shard's files.
A file is never split, so module-scoped fixtures (one Postgres container per file) run once.
The split is deterministic, so every job agrees on who runs what.

A file weighs its measured seconds from ``tests/shard_durations.json`` (integer milliseconds, so
a float never decides a tie). A file with no entry there (a new test file) weighs its test count
scaled by the seconds-per-test of the files that do have entries; a test marked ``slow`` (image
builds, packaged frontend) counts ``SLOW_WEIGHT`` tests. With an empty durations file the split
is the plain test-count one.

Refresh the seconds after a full run on ``main`` (see "Dev commands" in AGENTS.md)::

    gh run download <run-id> -p 'test-timings-shard-*'
    uv run python -m scripts.shard_durations merge test-timings-shard-*/junit-shard-*.xml \
        --out tests/shard_durations.json
    uv run python -m scripts.shard_durations report --counts 4,6,8

and commit the file in a reviewed pull request. A malformed durations file is an error, never a
silent fallback, because jobs that read different data would disagree about who runs what.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SLOW_WEIGHT = 20
ROOT = Path(__file__).resolve().parent.parent
DURATIONS_FILE = Path(__file__).resolve().parent / "shard_durations.json"


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


def load_durations(path: Path | None = None) -> dict[str, float]:
    """Recorded seconds per test file; a missing file is ``{}``, a malformed one is an error.

    Without a ``path`` the committed ``DURATIONS_FILE`` is read (looked up at call time).
    """
    path = DURATIONS_FILE if path is None else path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise ValueError(f"shard durations file {path} cannot be read: {error}") from error
    try:
        loaded = json.loads(text)
    except ValueError as error:
        raise ValueError(f"shard durations file {path} is not valid JSON: {error}") from error
    if not isinstance(loaded, dict):
        raise ValueError(f"shard durations file {path} must be a JSON object of seconds per file")
    durations: dict[str, float] = {}
    for module, seconds in loaded.items():
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, int | float)
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError(
                f"shard durations file {path}: {module!r} must be a finite number >= 0, "
                f"got {seconds!r}"
            )
        durations[module] = float(seconds)
    return durations


def file_weights(counts: Mapping[str, int], durations: Mapping[str, float]) -> dict[str, int]:
    """Integer milliseconds per file: recorded seconds, else the count scaled to match them."""
    recorded = {
        module: max(1, round(durations[module] * 1000)) for module in counts if module in durations
    }
    recorded_millis = sum(recorded.values())
    recorded_counts = sum(counts[module] for module in recorded)
    weights: dict[str, int] = {}
    for module, count in counts.items():
        if module in recorded:
            weights[module] = recorded[module]
        elif recorded_counts > 0:
            # Integer arithmetic (round half up): the same on every job whatever the key order.
            weights[module] = max(
                1, (2 * count * recorded_millis + recorded_counts) // (2 * recorded_counts)
            )
        else:
            weights[module] = max(1, count * 1000)
    return weights


def _module_key(path: Path) -> str:
    """The repo-relative posix path of a test file (pytest hands out absolute ones)."""
    if path.is_absolute():
        try:
            return path.relative_to(ROOT).as_posix()
        except ValueError:
            pass
    return path.as_posix()


def select(
    items: Sequence[Any], spec: str, durations: Mapping[str, float] | None = None
) -> tuple[list[Any], list[Any]]:
    """Split collected items into (this shard's, everyone else's)."""
    index, count = parse_shard(spec)
    recorded = load_durations() if durations is None else durations
    keys = [_module_key(item.path) for item in items]
    counts: dict[str, int] = {}
    for item, module in zip(items, keys, strict=True):
        counts[module] = counts.get(module, 0) + (
            SLOW_WEIGHT if item.get_closest_marker("slow") else 1
        )
    placed = shard_modules(file_weights(counts, recorded), count)
    kept: list[Any] = []
    dropped: list[Any] = []
    for item, module in zip(items, keys, strict=True):
        (kept if placed[module] == index else dropped).append(item)
    return kept, dropped
