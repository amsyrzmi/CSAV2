"""Turn pytest junit timings into the per-file seconds file the CI shard selector reads.

``merge`` reads one or more junit xml files and writes ``{"tests/test_x.py": seconds}``;
``report`` shows how a shard count would split those seconds, to choose the count.
Standard library only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path

DEFAULT_DURATIONS = Path("tests/shard_durations.json")
_TEST_FILE = re.compile(r"tests/test_[^/]+\.py")


class JunitError(Exception):
    """An input file that is missing or is not readable junit xml; the message names the file."""


def _resolve(case: ET.Element) -> str | None:
    """The repo-relative test file of a ``<testcase>``: its ``file`` attribute, else its class."""
    file_attr = (case.get("file") or "").replace("\\", "/")
    if _TEST_FILE.fullmatch(file_attr):
        return file_attr
    parts = (case.get("classname") or "").split(".")
    if len(parts) >= 2 and parts[0] == "tests" and parts[1].startswith("test_"):
        return f"tests/{parts[1]}.py"
    return None


def read_junit(path: Path) -> dict[str, float]:
    """Seconds per test file, summed over its cases; a skipped case counts 0.

    A case that cannot be tied to a ``tests/test_*.py`` file, or has no usable time, is left out
    with a warning on stderr. A file that cannot be read or parsed raises ``JunitError``.
    """
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as error:
        raise JunitError(f"cannot read junit file {path}: {error}") from error
    durations: dict[str, float] = {}
    ignored: list[str] = []
    for case in root.iter("testcase"):
        label = f"{case.get('classname') or '?'}::{case.get('name') or '?'}"
        module = _resolve(case)
        if module is None:
            ignored.append(label)
            continue
        if case.find("skipped") is not None:
            seconds = 0.0
        else:
            try:
                seconds = float(case.get("time") or 0.0)
            except ValueError:
                ignored.append(label)
                continue
            if not math.isfinite(seconds):
                ignored.append(label)
                continue
            seconds = max(0.0, seconds)
        durations[module] = durations.get(module, 0.0) + seconds
    if ignored:
        print(
            f"warning: {path}: ignored {len(ignored)} test case(s) with no resolvable test file "
            f"or time, e.g. {ignored[0]}",
            file=sys.stderr,
        )
    return durations


def merge(*parts: dict[str, float]) -> dict[str, float]:
    """Sum seconds per file across several readings."""
    total: dict[str, float] = {}
    for part in parts:
        for module, seconds in part.items():
            total[module] = total.get(module, 0.0) + seconds
    return total


def write_durations(durations: dict[str, float], path: Path) -> None:
    """Write sorted JSON, seconds to 0.1, only ``tests/*.py`` keys, with a trailing newline."""
    kept = {
        module: round(seconds, 1)
        for module, seconds in sorted(durations.items())
        if module.startswith("tests/") and module.endswith(".py")
    }
    path.write_text(json.dumps(kept, indent=2) + "\n", encoding="utf-8")


def report(durations: dict[str, float], count: int) -> str:
    """A plain-text view of splitting these files over ``count`` shards with the real algorithm."""
    from tests.shard import file_weights, shard_modules

    millis = file_weights(dict.fromkeys(durations, 1), durations)
    placed = shard_modules(millis, count)
    total = sum(millis.values()) / 1000
    lines = [f"shards: {count}  total: {total:.1f}  ideal: {total / count:.1f}"]
    for shard in range(1, count + 1):
        members = [module for module, number in placed.items() if number == shard]
        shard_seconds = sum(millis[module] for module in members) / 1000
        lines.append(f"shard {shard} {shard_seconds:.1f} s ({len(members)} files)")
    heaviest = sorted(millis, key=lambda module: (-millis[module], module))[:5]
    lines.append("heaviest:")
    lines.extend(f"  {millis[module] / 1000:.1f} s {module}" for module in heaviest)
    floor = max(millis.values(), default=0) / 1000
    lines.append(f"floor: {floor:.1f}")
    return "\n".join(lines)


def _counts(text: str) -> list[int]:
    try:
        counts = [int(part) for part in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError(f"counts must look like '4,6,8', got {text!r}") from None
    if not counts or any(count < 1 for count in counts):
        raise argparse.ArgumentTypeError(f"counts must be positive, got {text!r}")
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``merge`` or ``report`` command; 2 means an input file was missing or unreadable."""
    parser = argparse.ArgumentParser(prog="python -m scripts.shard_durations")
    commands = parser.add_subparsers(dest="command", required=True)
    merge_cmd = commands.add_parser("merge", help="junit xml files to a per-file seconds file")
    merge_cmd.add_argument("junit", nargs="+", type=Path)
    merge_cmd.add_argument("--out", type=Path, default=DEFAULT_DURATIONS)
    report_cmd = commands.add_parser("report", help="how a shard count would split the seconds")
    report_cmd.add_argument("--counts", type=_counts, default=[4, 6, 8])
    report_cmd.add_argument("--durations", type=Path, default=DEFAULT_DURATIONS)
    args = parser.parse_args(argv)

    try:
        if args.command == "merge":
            merged = merge(*(read_junit(path) for path in args.junit))
            try:
                write_durations(merged, args.out)
            except OSError as error:
                raise JunitError(f"cannot write durations file {args.out}: {error}") from error
            return 0
        from tests.shard import load_durations

        if not args.durations.is_file():
            raise JunitError(f"cannot read durations file {args.durations}: no such file")
        try:
            durations = load_durations(args.durations)
        except ValueError as error:
            raise JunitError(str(error)) from error
        print("\n\n".join(report(durations, count) for count in args.counts))
        return 0
    except JunitError as error:
        print(str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
