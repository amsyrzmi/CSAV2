# Planner prompt restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the JSON-schema half of the workspace planner's `SYSTEM_PROMPT` with a compact action catalogue generated from the contract classes, without any case getting worse on the smoke file.

**Architecture:** A pure function `compact_catalogue(schema)` in a new module renders `PlannerOutput.model_json_schema()` as one line per action (fields, `?` for optional, enums inline, nested models expanded) with each action's docstring under it, plus the notes of the nested models that carry rules. `planner.py` calls it in place of `json.dumps(schema)`. A small compare script turns two smoke outputs into a per-case pass/fail gate. Measurement uses the throwaway-container recipe already recorded beside the spec.

**Tech Stack:** Python 3.12, pydantic v2, pytest, ruff (line length 100), pyright.

**Spec:** `docs/superpowers/specs/2026-11-20-planner-prompt-restructure-design.md`. Read its "Starting point", "The gate" and "Smoke budget" parts first. Reference material (kept beside the spec): `docs/superpowers/specs/2026-11-20-planner-prompt-restructure-assets/variant_b_compact.py.txt` (the throwaway generator whose result is 474/495) and `throwaway_smoke_run.sh.txt` (the run recipe).

## Global Constraints

- Worktree `/home/amsyrzmi/CSAV2/.claude/worktrees/feat-planner-restructure`, branch `feat/planner-restructure`. No `.venv` there: run Python as `/home/amsyrzmi/CSAV2/.venv/bin/python`. The worktree Bash guard wants plain single commands (no `ENV=` prefixes, no compound commands); never disable git hooks.
- Ruff line length 100; run `ruff format` from the project (the editor hook un-wraps docstrings, re-check them).
- The repo is PUBLIC: synthetic data only; never print or write the model key, anything under `/srv/csa-staging/secrets`, or the gateway environment.
- The contract classes (`PlannerOutput` and every proposal class), executors, confirm flow, migrations and the console are NOT changed. Pydantic docstrings on contract classes are load-bearing prompt text: do not edit one except through the gate.
- Smoke budget (the owner approved 2026-11-20): up to 4 full runs at `--repeat 5` (baseline, reproduction of variant B, two candidates), targeted `--case` runs of up to 10 samples, and one final full staging run after merge. A fifth full run needs the owner's new yes. Run only in the throwaway container; never `docker exec` into staging for it before merge.
- Gate: same day, same model. Per case, candidate count >= baseline count - 1; a case 5/5 on the baseline stays >= 4/5; candidate total >= baseline total. Prompt ceiling: `len(SYSTEM_PROMPT) < 24_000`. If a regression cannot be fixed without hurting another case, stop and report to the owner; do not ship it.
- Do not deploy, merge or approve anything in this plan. Merge and the staging roll are the controller's step after green CI and review (standing authorisation); the production approval click is the owner's.

## Review Focus

- A model with a self-referencing field (a `$ref` back to itself): the generator must raise, not recurse forever (Task 1).
- A schema node the generator does not know (`allOf`, a `type` list like `["string","null"]`, `additionalProperties`, `prefixItems`): it must raise naming the node, not emit a wrong field (Task 1).
- A new contract class or nested model with a docstring that is neither an action nor listed in `NESTED_NOTES` / `NOTES_SKIPPED`: a test fails so a person decides whether its rule belongs in the prompt (Task 1).
- Two smoke outputs at different `--repeat` values, or a case present in only one: the compare script fails with a clear line instead of comparing wrong numbers or crashing (Task 2).
- A docstring or note containing `{`, `}` or quotes: the prompt still builds and the catalogue text is unchanged by f-string formatting (Task 3).

---

### Task 1: The catalogue generator

**Files:**
- Create: `csa/workspace/planner_catalogue.py`
- Create: `tests/test_planner_catalogue.py`

**Interfaces:**
- Consumes: `csa.workspace.planner.PlannerOutput` (tests only; the module itself takes a schema dict and must not import `planner.py`, to avoid an import cycle).
- Produces:
  - `compact_catalogue(schema: dict[str, Any]) -> str`
  - `NESTED_NOTES: tuple[str, ...]` = `("BookingFreeAsk", "ClarificationOption", "RecipientSpec", "RecordRef")` (the four notes variant B carried, in sorted order)
  - `NOTES_SKIPPED: frozenset[str]` (the non-action `$defs` names that have a description but are deliberately not in the prompt; fill from the current schema in step 3)

- [ ] **Step 1: Write the failing tests** in `tests/test_planner_catalogue.py`:
  - `test_every_action_is_listed_with_every_field`: for each `(action, ref)` in `schema["properties"]["proposal"]["discriminator"]["mapping"]`, `f"- {action}:"` is in the catalogue, and every field name of that class other than `action` appears inside that action's line (split the catalogue on `"\n- "`).
  - `test_optional_fields_are_marked_and_required_are_not`: assert on a tiny hand-written schema dict (one required string field, one optional): `name: string` and `note?: string`.
  - `test_action_docstring_follows_its_line`: the whitespace-collapsed `description` of `RecipientSpec`'s owner action (any action class with a description) appears in the catalogue.
  - `test_nested_notes_are_present`: for each name in `NESTED_NOTES` the collapsed description of that `$defs` entry is in the catalogue.
  - `test_every_described_nested_model_is_decided`: every `$defs` name that is not an action class and has a `description` is in `NESTED_NOTES` or `NOTES_SKIPPED` (the message names the missing class and says to add it to one of them).
  - `test_enums_dates_and_nullables_render`: hand-written schema with an `enum`, a `format: date` string, an `anyOf` with `{"type":"null"}` and an array of objects renders `"a"|"b"`, `"YYYY-MM-DD"`, the non-null type only, and `[{...}]`.
  - `test_unknown_nodes_raise`: for `{"allOf": [...]}`, `{"type": ["string","null"]}`, `{"type":"object","additionalProperties":{"type":"string"}}`, `{"prefixItems": [...]}` the function raises `ValueError` whose message contains the node kind (`allOf`, a type list, `additionalProperties`, `prefixItems`).
  - `test_self_reference_raises`: a `$defs` entry whose field `$ref`s itself raises `ValueError` mentioning "recursive".
  - `test_real_schema_is_smaller_than_json`: `len(compact_catalogue(schema)) < len(json.dumps(schema, separators=(",", ":"))) * 0.7`.

- [ ] **Step 2: Run to verify they fail**
  Run: `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_planner_catalogue.py -q`
  Expected: FAIL (module not found).

- [ ] **Step 3: Implement `compact_catalogue`** in `csa/workspace/planner_catalogue.py`, starting from `variant_b_compact.py.txt` (same output notation, so the reproduction run in Task 4 measures what variant B measured). Changes from the throwaway: type hints and a module docstring; a `_Unsupported` check that raises `ValueError(f"planner catalogue cannot render {kind}")` for any node with a key outside `{$ref, const, enum, anyOf, oneOf, type, items, properties, required, format, description, title, default, discriminator}` or a list-valued `type`, or `additionalProperties`/`allOf`/`prefixItems`; a `seen` stack of `$ref` names that raises `ValueError("planner catalogue: recursive model <name>")`; the notes loop iterates `NESTED_NOTES`. Compute `NOTES_SKIPPED` by running the real schema once in a REPL (non-action `$defs` with a description minus `NESTED_NOTES`) and write the literal frozenset.

- [ ] **Step 4: Run to verify they pass**
  Run: `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_planner_catalogue.py -q`
  Expected: all pass.

- [ ] **Step 5: Lint and commit**
  Run: `/home/amsyrzmi/CSAV2/.venv/bin/ruff check csa/workspace/planner_catalogue.py tests/test_planner_catalogue.py` then `/home/amsyrzmi/CSAV2/.venv/bin/ruff format csa/workspace/planner_catalogue.py tests/test_planner_catalogue.py` (as separate commands), then `git add` those two files and commit `feat(planner): generated compact action catalogue` with the attribution trailer.

### Task 2: The smoke compare script

**Files:**
- Create: `scripts/smoke_compare.py`
- Create: `tests/test_smoke_compare.py`

**Interfaces:**
- Consumes: smoke output text with lines `RATE <case id> <k>/<N>` (see `scripts/assistant_smoke.py`, the `out(f"RATE ...")` line). Lines of other kinds are ignored.
- Produces:
  - `parse_rates(text: str) -> dict[str, tuple[int, int]]` (case id to `(wins, samples)`)
  - `compare(base: dict[str, tuple[int, int]], cand: dict[str, tuple[int, int]]) -> list[str]` (violation lines; empty means the gate passes)
  - `main(argv: Sequence[str] | None = None) -> int` with `smoke_compare.py BASELINE_FILE CANDIDATE_FILE`; prints one `UP`/`DOWN`/`SAME`-style line per changed case, a totals line, and each violation as `VIOLATION ...`; exit 1 on any violation, 0 otherwise.

- [ ] **Step 1: Write the failing tests** in `tests/test_smoke_compare.py`: `parse_rates` reads `RATE a-1 5/5`, ignores `FAIL ...` and the summary line; `compare` returns no violation for equal and for 5/5 to 4/5 on one case when the total is not lower (offset by an `UP` elsewhere); a violation for 5/5 to 3/5; a violation when the total is lower; a violation naming the case when it is missing from the candidate; a violation when samples differ (`5` vs `10`) that says "different --repeat"; `main` returns 1 and prints `VIOLATION` for a bad pair of files (use `tmp_path`) and 0 for a good pair.

- [ ] **Step 2: Run to verify they fail**
  Run: `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_smoke_compare.py -q`
  Expected: FAIL (module not found).

- [ ] **Step 3: Implement** the three functions in `scripts/smoke_compare.py`: the three gate rules from the spec's "The gate" (per case `cand >= base - 1`; a baseline `N/N` case keeps `cand >= N - 1`; total of candidate wins `>=` total of baseline wins); a case present in only one file is a violation; mismatched sample counts abort comparison with the single "different --repeat" violation. Output contains only case ids and counts.

- [ ] **Step 4: Run to verify they pass**
  Run: `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_smoke_compare.py -q`
  Expected: all pass.

- [ ] **Step 5: Lint and commit** as in Task 1 (`feat(scripts): smoke_compare gate for prompt changes`).

### Task 3: Baseline run (measurement only, no code change)

**Files:** none committed. Outputs go to the scratchpad `/tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/restructure/`.

**Interfaces:**
- Consumes: the unmodified `planner.py` at this branch's current commit (identical to main for `csa/`), the recipe `throwaway_smoke_run.sh.txt` (copy it to the scratchpad as `run.sh`, `chmod +x`).
- Produces: `baseline.txt`, the same-day reference for every later comparison. Counts toward full run 1 of 4.

- [ ] **Step 1: Confirm `csa/workspace/planner.py` has no diff against `origin/main`**: `git diff origin/main -- csa scripts tests` is empty except Tasks 1-2's new files (neither is imported by the planner yet).
- [ ] **Step 2: Run the whole file at 5 samples** in the background with output to `baseline.txt`: `<scratchpad>/restructure/run.sh /home/amsyrzmi/CSAV2/.claude/worktrees/feat-planner-restructure 5`. It takes roughly 20 to 30 minutes; wait with a monitor (`[p]ython`-style bracket in any `pgrep`), not a sleep.
- [ ] **Step 3: Check it** ends with a line `N passed, M failed` and has 99 `RATE` lines; report the totals. If there is a setup error, report it and stop (do not rerun without the controller).

### Task 4: Wire the catalogue in and reproduce variant B

**Files:**
- Modify: `csa/workspace/planner.py` (the `SYSTEM_PROMPT` f-string near line 285)
- Modify: `tests/test_workspace_planner.py` (add the size and shape tests at the end of the prompt-related tests)

**Interfaces:**
- Consumes: `compact_catalogue` (Task 1), `baseline.txt` and `smoke_compare.py` (Tasks 2-3).
- Produces: `SYSTEM_PROMPT` without the schema JSON; a candidate output `candidate1.txt`.

- [ ] **Step 1: Write the failing tests** in `tests/test_workspace_planner.py`: `test_system_prompt_is_the_compact_catalogue` asserts `len(SYSTEM_PROMPT) < 24_000`, `'"$defs"' not in SYSTEM_PROMPT`, `compact_catalogue(PlannerOutput.model_json_schema()) in SYSTEM_PROMPT`, and that every action name from the discriminator mapping appears as `f"- {name}:"`. `test_catalogue_with_braces_builds_a_prompt`: calling the same f-string composition path with a catalogue string containing `{`/`}` leaves it unchanged (assert the text of `SYSTEM_PROMPT` between the schema anchor and the "Facts:" line equals the catalogue). Run them: they must FAIL.
- [ ] **Step 2: Edit `planner.py`**: add `from csa.workspace.planner_catalogue import compact_catalogue` with the other imports (no `noqa`, no mid-file import) and replace `{json.dumps(PlannerOutput.model_json_schema(), separators=(",", ":"))}` with `{compact_catalogue(PlannerOutput.model_json_schema())}`. Change the sentence "matching this JSON schema:" to "using these actions:" ONLY if the reproduction run in Step 4 does not match B; otherwise leave the prose exactly as the experiment had it (the experiment left it unchanged).
- [ ] **Step 3: Run the planner tests**
  Run: `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_workspace_planner.py tests/test_planner_catalogue.py tests/test_assistant_smoke.py -q`
  Expected: all pass (existing tests check schema content or prose, not schema text inside the prompt; if one fails, read it and report which assertion, do not weaken it).
- [ ] **Step 4: Reproduction run** (full run 2 of 4): `run.sh <worktree> 5` into `candidate1.txt`, then `/home/amsyrzmi/CSAV2/.venv/bin/python -m scripts.smoke_compare baseline.txt candidate1.txt`. Expected: close to variant B (about 474/495, with `attention-en-1`, `directory-ms-3`, `email_batch_group-ms-1` DOWN and the Malay HR reads UP). Print `len(SYSTEM_PROMPT)`; expected near 21,889. A large difference means the generator drifted from the throwaway: diff its output against `variant_b_compact.py.txt`'s output and fix the module before going on.
- [ ] **Step 5: Lint and commit** (`feat(planner): use the compact generated catalogue in SYSTEM_PROMPT`), including no output files.

### Task 5: Fix the three regressions

**Files:**
- Modify: `csa/workspace/planner.py` (prose lines next to `clarification`, `email_batch`, and wherever Step 1 points), possibly `csa/workspace/planner_catalogue.py` for notation.
- Modify: `tests/test_workspace_planner.py` (one test per added sentence, asserting the exact text, in the style of the existing `test_planner_prompt_states_*` tests).

**Interfaces:**
- Consumes: the reproduction result (Task 4). Produces: `candidate2.txt` and a per-case table.

- [ ] **Step 1: Diagnose `attention-en-1`.** In a scratch copy of the worktree (under the scratchpad, never committed), add a temporary print of the pydantic `ValidationError.errors()` `loc` and `type` fields (not input values) where `GroqClient.json_completion` validates, and run `run.sh <scratch copy> 1 attention-en-1`. Report which field or action name breaks the contract. Then decide the cheapest catalogue or prose change that stops it.
- [ ] **Step 2: Add the three fixes**, one at a time, each followed by a targeted run of up to 10 samples of that case plus its sibling cases (`directory-ms-1..3` for the directory fix, `email_batch_group-*` and `email_batch_rest-*` for the mode fix, `attention-*` for the third): an `email_batch` reminder that a request for "their own" date or detail is `personalised`, even in Malay, placed in the action's catalogue note or its prose bullet; a note beside `clarification` that its option values are real ids or labels the user can pick, never placeholder words like `directory_lookup` or `type_address`, and that an unknown recipient is a `directory_lookup` action, not a clarification; and the `attention-en-1` fix from Step 1. Each fix gets its test asserting the exact sentence. Keep a running count of prompt characters; it must stay under 24,000.
- [ ] **Step 3: Full run 3 of 4** into `candidate2.txt`; run `smoke_compare.py baseline.txt candidate2.txt`. If the gate passes, continue. If it does not, one more round of fixes and full run 4 of 4. If still failing, STOP and report the table to the controller for the owner's decision.
- [ ] **Step 4: Planner tests and lint** as in Task 4 Step 3, then `ruff check`, `ruff format` and `pyright` on the changed files (separate commands); commit (`fix(planner): wording for the three compact-catalogue regressions`).

### Task 6: Record it

**Files:**
- Modify: `docs/PROGRESS.md` (top entry "Planner prompt restructure (2026-11-20 …)")
- Modify: `docs/superpowers/specs/2026-11-20-planner-prompt-restructure-design.md` (add the final numbers under "Measured so far")

- [ ] **Step 1: Write the entry** with: baseline total and cases at 5/5, final candidate total and cases at 5/5, characters before (36,402) and after, the per-case UP/DOWN table from `smoke_compare`, what each of the three fixes was, and the still-open cases (`hr_process_neg-ms-5` and any flaky ones). Case ids and counts only; no message text.
- [ ] **Step 2: Commit** (`docs: planner restructure results`), then push the branch and report to the controller: the commit list, the final compare output, `make lint`-equivalent results on the changed files. The controller opens the PR, waits for CI and review, merges, and does the staging roll.

---

## Self-review (done)

- **Spec coverage:** generator (Task 1), compare script and gate (Task 2), baseline same-day (Task 3), wiring, ceiling, reproduction of B (Task 4), three regressions and the stop rule (Task 5), PROGRESS and numbers (Task 6). The final staging smoke after merge is the controller's step, outside this plan.
- **Type consistency:** `compact_catalogue`, `NESTED_NOTES`, `NOTES_SKIPPED`, `parse_rates`, `compare`, `main` are named the same in every task that uses them.
- **Proportion:** the plan is shorter than the code it describes plus the measurement steps; function bodies are not written out, the experiment file is the reference.
