# CI shard rebalance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Balance the CI test shards by measured seconds per test file and raise the shard count from 4 to 6, so the slowest shard drops from about 54 to about 28 minutes at about the same total cost.

**Architecture:** Two pull requests. PR A (Tasks 1 to 3) adds the machinery with no behaviour change: a script that turns pytest junit files into a per-file seconds file, a shard selector that weighs files by those seconds (falling back to today's test-count weight when the file has no entry), and CI steps that record and upload timings from every sharded run. After PR A merges and a full run has produced timings, PR B (Task 4, run by the controller) commits the measured `tests/shard_durations.json` and changes the matrix from 4 to 6 shards.

**Tech Stack:** Python 3.12, pytest (junitxml), GitHub Actions (artifacts), `gh` CLI for the one-time download.

**Spec:** `docs/superpowers/specs/2026-11-13-mito-rename-and-org-move-design.md` (section 6).

## Global Constraints

- The split stays deterministic: every shard job computes the same assignment from the same inputs. Weights are integers (milliseconds) so floating point never decides a tie.
- Files are never split (module-scoped Postgres fixtures run once per file); `make test` and a local run without `CSA_TEST_SHARD` still run everything; no test is skipped or duplicated.
- With an empty or missing `tests/shard_durations.json` the selector behaves exactly as today (test count, slow = `SLOW_WEIGHT`). PR A must not change which tests run on which shard.
- Pull requests keep running only the changed test files (shard 0). Do not change what PRs run, the Postgres image handling, or any test's content.
- `tests/shard_durations.json` is changed only by a reviewed pull request, never by an automatic commit.
- New code under `scripts/` and `tests/`; Python 3.12, type hints, `ruff` line length 100 as the project uses; `make lint` must pass.
- Run tests one file at a time: `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/python -m pytest <file> -q`. No `ENV=` prefixes or compound commands in one call (worktree guard). CI is the merge gate.
- A PostToolUse formatter hook may reformat files after the Edit tool (line length 120, the project uses 100): use Write for new files, python replace scripts for edits, then check `git diff --stat` and run the project `ruff format` on touched files.
- Commit trailers: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_012Xj1GSBskmizHEtGV54ZTA`.

## Review Focus

- A file that has a recorded duration of 0 or a tiny value must still get a shard and must not make the fallback ratio zero or divide by zero.
- A brand-new test file (no entry) among entries that are all large or all small must land sensibly: its fallback weight is scaled from the recorded files, not left at "1".
- Two jobs that see the entries in a different order (the JSON key order) must agree on the assignment.
- A durations file that is not valid JSON, or has a negative or non-number value, must fail loudly with a message naming the file, not silently fall back (a silent fallback would make shards disagree if only some jobs could read it).
- Junit input with a test case without a `file` or `classname` that maps to a test file, a skipped case, or the same file in two junit inputs (two shards never overlap, but a re-run might) must be handled: skipped counts 0, unknown names are ignored with a warning, duplicates are summed.

---

### Task 1: `scripts/shard_durations.py` (junit to per-file seconds)

**Files:**
- Create: `scripts/shard_durations.py`
- Test: `tests/test_shard_durations.py`

**Interfaces:**
- Consumes: pytest junit xml (a `<testcase classname="tests.test_x" name="..." time="1.23" file="tests/test_x.py">` element; newer pytest writes `file`; derive `tests/<module>.py` from `classname` when `file` is absent, taking the first two dotted parts when the class is nested, e.g. `tests.test_x.TestY` becomes `tests/test_x.py`).
- Produces:
  - `read_junit(path: Path) -> dict[str, float]`: seconds per repo-relative test file path summed over its test cases; skipped cases count 0; an element whose file cannot be resolved to a `tests/test_*.py` string is ignored.
  - `merge(*parts: dict[str, float]) -> dict[str, float]`: sums per key.
  - `write_durations(durations: dict[str, float], path: Path) -> None`: JSON object, keys sorted, values rounded to 0.1 s, trailing newline, only keys that start with `tests/` and end with `.py`.
  - `load_durations(path: Path) -> dict[str, int]` (used by Task 2, lives in `tests/shard.py`, not here: see Task 2).
  - `report(durations: dict[str, float], count: int) -> str`: a plain-text table for a proposed shard count: per-shard total seconds under the real algorithm (`tests.shard.shard_modules` on millisecond integer weights), the ideal share `total / count`, the heaviest five files, and a line `floor: <seconds>` equal to the largest single file; used to choose the shard count.
  - CLI: `python -m scripts.shard_durations merge JUNIT... --out tests/shard_durations.json` and `python -m scripts.shard_durations report --counts 4,6,8` (reads `tests/shard_durations.json`). Exit 2 with a message on missing or unparsable input.

- [ ] **Step 1: Write the tests** in `tests/test_shard_durations.py` with small junit strings written to `tmp_path`: (a) `read_junit` sums two cases of one file and keeps files separate; (b) a skipped case (`<skipped .../>` child) counts 0; (c) a case with only `classname="tests.test_x.TestY"` resolves to `tests/test_x.py`; (d) a case with an unresolvable classname is ignored; (e) `merge` sums a file present in both parts; (f) `write_durations` writes sorted keys, one decimal, ends with a newline, drops keys that are not `tests/*.py`; (g) `report([...], 2)` lists two shards whose totals equal the input total (within rounding) and includes a `floor:` line equal to the largest file; (h) the CLI `merge` on two junit files writes the expected JSON and exits 0; a missing file exits 2.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** `scripts/shard_durations.py` (standard library `xml.etree.ElementTree`; no new dependency; import `shard_modules` from `tests.shard` inside `report`). **Step 4: run `tests/test_shard_durations.py`: PASS.** Commit `feat(ci): turn pytest junit timings into a per-file seconds file`.

### Task 2: The selector weighs files by recorded seconds

**Files:**
- Modify: `tests/shard.py`
- Create: `tests/shard_durations.json` (content exactly `{}` plus a trailing newline)
- Modify: `tests/test_ci_sharding.py`

**Interfaces:**
- Consumes: Task 1's file format (`{"tests/test_x.py": 12.3, ...}`).
- Produces in `tests/shard.py`:
  - `DURATIONS_FILE: Path` (`tests/shard_durations.json` next to the module) and `load_durations(path: Path = DURATIONS_FILE) -> dict[str, float]`: reads the JSON object; a missing file returns `{}`; invalid JSON, a non-object, or a value that is not a finite number `>= 0` raises `ValueError` whose message contains the file name and the word `shard`.
  - `file_weights(counts: dict[str, int], durations: dict[str, float]) -> dict[str, int]`: `counts` is the old weight per file (test count, slow = `SLOW_WEIGHT`); the result is integer milliseconds: a file with an entry weighs `max(1, round(seconds * 1000))`; a file without an entry weighs `max(1, round(counts[file] * ratio * 1000))` where `ratio = sum(seconds of files that have entries) / sum(counts of those same files)`, and `ratio = 1.0` when no collected file has an entry or the sums are zero (so an empty durations file reproduces today's relative weights).
  - `select(items, spec, durations=None)`: unchanged signature plus the optional `durations` argument (default: `load_durations()`), building `counts` as today, then `placed = shard_modules(file_weights(counts, durations), count)`.

- [ ] **Step 1: Extend `tests/test_ci_sharding.py`** (keep every existing test passing unchanged): (a) with `durations={}` `select` assigns exactly as the old count-based method for the existing 60-item example (compare with a direct `shard_modules(counts, n)`); (b) a file with a large recorded duration and few tests is placed alone against many small-duration files (assert the loads by seconds are within the largest file of each other); (c) a new file without an entry gets a fallback weight scaled by the ratio: with `durations={"tests/test_a.py": 100.0}` and 10 tests in `test_a.py` and 10 in `test_new.py`, `file_weights` gives both about 100000 ms; (d) an entry of 0 seconds weighs 1, never 0, and does not zero the ratio denominator; (e) key order of `durations` does not change `select` results; (f) `load_durations` raises `ValueError` mentioning the file name for invalid JSON, a list, a negative number and a string value, returns `{}` for a missing file, and loads a valid object; (g) every test is kept by exactly one shard for 6 shards and a durations dict covering only some files; (h) the committed `tests/shard_durations.json` loads without error.
- [ ] **Step 2: Run `tests/test_ci_sharding.py`, expect the new tests FAIL.** **Step 3: Implement** in `tests/shard.py`, update its module docstring (weights are measured seconds, with the count fallback, and say how to refresh: see Task 3), create `tests/shard_durations.json` as `{}`. **Step 4: run `tests/test_ci_sharding.py`: PASS.** Commit `feat(ci): weigh test files by recorded seconds, falling back to test count`.

### Task 3: CI records timings; refresh procedure documented

**Files:**
- Modify: `.github/workflows/ci.yml` (the `tests` job only)
- Modify: `AGENTS.md` (one bullet under "Dev commands")
- Modify: `tests/shard.py` docstring (the refresh command, if Task 2 did not already include it)

**Interfaces:**
- Produces: for shards 1 to N (not shard 0) the `Pytest` step also writes `junit-shard-<SHARD>.xml` (`--junitxml`) and a following `actions/upload-artifact@v4` step uploads it as `test-timings-shard-<SHARD>` with `if: always()` and `retention-days: 30`; shard 0 (changed files) is unchanged. Nothing else in the job changes.

- [ ] **Step 1:** Edit the `Pytest` step: in the sharded branch run `CSA_TEST_SHARD="$SHARD/$SHARD_TOTAL" uv run pytest --junitxml="junit-shard-$SHARD.xml"`; add the upload step (condition `matrix.shard != 0`). Keep every other line. Update the comment above `strategy` only if it says "four" (do not change the matrix in this PR).
- [ ] **Step 2:** Add to `AGENTS.md` under "Dev commands" a bullet that explains the refresh: download the `test-timings-shard-*` artifacts of a successful main run (`gh run download <run-id> -n test-timings-shard-1 ...` or `-p 'test-timings-shard-*'`), run `uv run python -m scripts.shard_durations merge <each junit-shard-N.xml> --out tests/shard_durations.json`, then `uv run python -m scripts.shard_durations report --counts 4,6,8`, and commit the file in a reviewed pull request; the selector falls back to test count for a file with no entry.
- [ ] **Step 3: Verify.** YAML must stay valid and the changed-files path must be byte-identical in behaviour: run `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` (if `yaml` is not installed in the venv, use any available YAML check and say which), and `actionlint` if it is installed. Run `make lint`. Commit `ci: record per-file test timings from the sharded runs and document the refresh`.

### Task 4 (controller, after PR A is merged): measure, commit the durations, go to 6 shards

Not given to a subagent. After PR A merges and CI on `main` has run the four shards (about an hour):
1. Download the four `test-timings-shard-*` artifacts of that run, merge them with `scripts.shard_durations merge`, run `report --counts 4,6,8` and check the `floor:` line: if the heaviest single file exceeds the ideal share at 6, report it to the owner and choose the count from the data.
2. On a new branch commit `tests/shard_durations.json`, change the matrix in `.github/workflows/ci.yml` from `[1,2,3,4]` to the chosen count (default `[1,2,3,4,5,6]`) and the comment that says "four", keep `SHARD_TOTAL: ${{ strategy.job-total }}`.
3. Open the pull request, and after it merges compare the next main run's slowest shard with the 54 minutes before (expected about 28). Record the result in `docs/PROGRESS.md`.
