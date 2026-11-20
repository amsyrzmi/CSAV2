"""Compare two ``assistant_smoke --repeat N`` outputs case by case (the prompt-change gate).

Usage: ``python -m scripts.smoke_compare BASELINE_FILE CANDIDATE_FILE``

Both files are the output of the same ``assistant_smoke`` run settings, taken the same day on the
same model. The candidate passes when no case falls by more than one sample, no case that passed
every sample on the baseline falls below all-but-one, and the total does not fall. Output is case
ids and counts only, never message text.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

Rates = dict[str, tuple[int, int]]
_RATE = re.compile(r"^RATE (\S+) (\d+)/(\d+)$")
MARGIN = 1


def parse_rates(text: str) -> Rates:
    """Case id to ``(wins, samples)`` from the ``RATE <id> k/N`` lines; other lines are ignored."""
    rates: Rates = {}
    for line in text.splitlines():
        match = _RATE.match(line.strip())
        if match:
            rates[match.group(1)] = (int(match.group(2)), int(match.group(3)))
    return rates


def compare(base: Rates, cand: Rates) -> list[str]:
    """The gate's violations as printable lines; an empty list means the candidate passes."""
    samples = {n for _, n in (*base.values(), *cand.values())}
    if len(samples) > 1:
        return ["VIOLATION different --repeat in the two files: " + ", ".join(map(str, samples))]
    violations: list[str] = []
    for case, (wins, n) in sorted(base.items()):
        if case not in cand:
            violations.append(f"VIOLATION {case}: missing from the candidate")
            continue
        now = cand[case][0]
        if now < wins - MARGIN or (wins == n and now < n - MARGIN):
            violations.append(f"VIOLATION {case}: {wins}/{n} to {now}/{n}")
    violations.extend(
        f"VIOLATION {case}: missing from the baseline" for case in sorted(set(cand) - set(base))
    )
    base_total = sum(w for w, _ in base.values())
    cand_total = sum(w for case, (w, _) in cand.items() if case in base)
    if cand_total < base_total:
        violations.append(f"VIOLATION total {base_total} to {cand_total} samples passing")
    return violations


def main(argv: Sequence[str] | None = None) -> int:
    """Print the per-case changes, the totals and any violation; exit 1 when there is one."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        print("usage: smoke_compare BASELINE_FILE CANDIDATE_FILE", file=sys.stderr)
        return 2
    base = parse_rates(Path(args[0]).read_text())
    cand = parse_rates(Path(args[1]).read_text())
    for case in sorted(set(base) & set(cand)):
        (was, n), (now, _) = base[case], cand[case]
        if now != was:
            print(f"{'UP' if now > was else 'DOWN'} {case} {was}/{n} to {now}/{n}")
    n_all = next(iter(base.values()), (0, 0))[1]
    shared = set(base) & set(cand)
    base_total = sum(base[c][0] for c in shared)
    cand_total = sum(cand[c][0] for c in shared)
    print(f"total {base_total}/{len(shared) * n_all} to {cand_total}/{len(shared) * n_all}")
    violations = compare(base, cand)
    for line in violations:
        print(line)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
