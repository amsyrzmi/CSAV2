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


def _totals(base: Rates, cand: Rates) -> tuple[int, int, int]:
    """Samples passing on the baseline, on the candidate (baseline cases only), and possible."""
    base_wins = sum(w for w, _ in base.values())
    cand_wins = sum(cand[case][0] for case in base if case in cand)
    return base_wins, cand_wins, sum(n for _, n in base.values())


def compare(base: Rates, cand: Rates) -> list[str]:
    """The gate's violations as printable lines; an empty list means the candidate passes."""
    for name, rates in (("baseline", base), ("candidate", cand)):
        if not rates:
            return [f"VIOLATION no RATE lines in the {name} file (was --repeat 2 or more used?)"]
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
    base_total, cand_total, _ = _totals(base, cand)
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
    violations = compare(base, cand)
    # With no RATE lines or different sample counts there is nothing meaningful to print.
    if not any("no RATE lines" in v or "different --repeat" in v for v in violations):
        for case in sorted(set(base) & set(cand)):
            (was, n), (now, _) = base[case], cand[case]
            if now != was:
                print(f"{'UP' if now > was else 'DOWN'} {case} {was}/{n} to {now}/{n}")
        base_total, cand_total, possible = _totals(base, cand)
        print(f"total {base_total}/{possible} to {cand_total}/{possible}")
    for line in violations:
        print(line)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
