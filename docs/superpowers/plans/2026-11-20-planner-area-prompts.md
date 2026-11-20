# Planner area prompts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each planner turn sees only the general prompt plus the areas it needs (selected by deterministic server code, later helped by the routing decider), behind a switch that is off by default, measured case by case against the full prompt.

**Architecture:** The full prompt is split into a head, a schema and a list of prose bullets, each bullet tagged with the areas it belongs to; `build_prompt(schema, areas)` assembles the full prompt (areas `None`, byte-identical to today) or a scoped one (the schema filtered to the selected actions). A pure selector maps message, tab, open record, previous turn and the existing fact `Needs` to a set of areas, or `None` (use the full prompt). `PlannerFacts.areas` carries the selection to `WorkspacePlanner.plan`; `_corrected_plan` widens to the full prompt once on an out-of-area action or a broken contract. Phase B adds one yes/no question per area to the routing decider's existing request.

**Tech Stack:** Python 3.12, pydantic v2, pytest, ruff (line length 100), pyright, import-linter; the smoke harness `scripts/assistant_smoke.py` and the gate `scripts/smoke_compare.py` (PR #170).

**Spec:** `docs/superpowers/specs/2026-11-20-planner-area-prompts-design.md` (read all of it, including "Decisions I made after the builder check"). Earlier evidence: `docs/superpowers/specs/2026-11-20-planner-prompt-restructure-design.md`, "Results of the build". Run recipe for the smoke in a throwaway container: `docs/superpowers/specs/2026-11-20-planner-prompt-restructure-assets/throwaway_smoke_run.sh.txt`.

## Global Constraints

- Worktree `/home/amsyrzmi/CSAV2/.claude/worktrees/feat-area-prompts`, branch `feat/area-prompts`. No `.venv` there: run Python as `/home/amsyrzmi/CSAV2/.venv/bin/python`. The Bash guard wants plain single commands (no `ENV=` prefix, no compound commands); never disable git hooks.
- **The edit hook reformats whole `.py` files with a stricter config and can delete an import it thinks unused.** Edit `csa/workspace/planner.py` and `csa/workspace/service.py` with a Python replace script through Bash (assert each old string occurs once), then run `/home/amsyrzmi/CSAV2/.venv/bin/ruff format` on the changed files (project config, line length 100) and `ruff check`.
- The repo is PUBLIC: synthetic data only; never print or write the model key, the Jev key or anything under `/srv/csa-staging/secrets`.
- Unchanged: the contract classes and `PlannerOutput` (validation still accepts every action), executors, confirm flow, migrations, console, model, temperature, token limits. The scoped prompt is behind the switch `WORKSPACE_AREA_PROMPTS`, default `False`.
- `SYSTEM_PROMPT` (the full prompt) must stay byte-identical to today's. About 43 assertions in `tests/test_workspace_planner.py` read it and keep passing unchanged (a ruling against the spec's "they move": identity makes moving them churn; scoped assertions are new tests).
- Smoke budget (owner approved the design 2026-11-20; ask for the yes before the first run): up to 5 full runs at `--repeat 5` (baseline + candidates), targeted `--case` runs of up to 10 samples, one final staging run after the switch is on; a sixth full run needs a new yes. Smoke runs only in the throwaway container.
- Gate (design, "Measuring"): same-day baseline (full prompt) and candidate (scoped, `--no-widen`); no case falls by more than one sample, a baseline-5/5 case stays >= 4/5, the total does not fall, the widened share is reported per area (ceiling 10%). A violating case is rerun alone at `--repeat 10` before it blocks. If a regression cannot be fixed without hurting another case, stop and report to the owner.
- Do not deploy, flip a switch, merge or approve in this plan. The Jev evaluation (`scripts/assistant_jev_eval.py`) is run by the owner with their own key.

## Review Focus

- A new action added to `PlannerProposal` with no area, or a prose bullet with no area tag, must fail a test (Task 1).
- `build_prompt(schema, None)` must equal today's `SYSTEM_PROMPT` exactly, since the baseline and the fail-open path are that prompt (Task 1).
- Every selected area's actions must arrive with their own bullet and every rule that names them, and the scoped schema must stay valid JSON listing only allowed actions (Task 2).
- A message with no signal, an empty history and an unknown tab selects `None`; `None` and the switch off both give the full prompt, and `facts.areas` never reaches the model's JSON (Tasks 3 and 4).
- The widening path: out-of-area action, or `PlannerInvalidOutput` with no named correction note while scoped, gives exactly one more call with the full prompt that is final; with the switch off nothing changes and no extra call is ever made (Task 4).

---

### Task 1: Area map and the prompt as tagged bullets (no behaviour change)

**Files:**
- Create: `csa/workspace/prompt_areas.py`, `tests/test_prompt_areas.py`, `tests/fixtures/planner_system_prompt_full.txt`
- Modify: `csa/workspace/planner.py` (the `SYSTEM_PROMPT` definition near line 285)

**Interfaces:**
- Produces:
  - `Area = Literal["email", "inbox", "training", "hr", "bookings", "products"]`; `AREAS: tuple[Area, ...]`
  - `ACTION_AREA: dict[str, Area | Literal["general"]]` covering every action in `PlannerOutput`'s discriminator mapping; `plan` is tagged `bookings` and travels with the four areas in Task 2
  - `Bullet` (frozen dataclass: `text: str`, `areas: frozenset[Area | Literal["general"]]`) and `BULLETS: tuple[Bullet, ...]`, the 50-odd prose bullets after "Facts:" in today's order, each tagged (a bullet naming actions of two areas carries both; fact bullets follow the area that uses the fact; closing rules are `general`)
  - `build_prompt(schema: dict[str, Any], areas: frozenset[Area] | None) -> str`; with `None` it returns the full prompt
- Consumes: nothing from other tasks. `prompt_areas.py` takes the schema as an argument and must not import `planner.py` (planner imports it).

- [ ] **Step 1: Capture the golden prompt.** From the worktree's current code, write `SYSTEM_PROMPT` verbatim to `tests/fixtures/planner_system_prompt_full.txt` (a one-off Python command; commit the file).
- [ ] **Step 2: Write the failing tests** in `tests/test_prompt_areas.py`: `test_full_prompt_is_identical_to_the_golden_file` (`build_prompt(PlannerOutput.model_json_schema(), None) == golden` and `SYSTEM_PROMPT == golden`); `test_every_action_has_an_area` (every name in the schema's discriminator mapping is a key of `ACTION_AREA`, and no key is unknown; the failure message names the action and says to add it); `test_every_bullet_has_at_least_one_area`; `test_every_bullet_that_names_an_action_carries_that_actions_area` (for each action name, each bullet whose text contains it as a whole word has that action's area among its areas, or is `general`); `test_bullets_cover_the_prose_exactly` (joining `BULLETS` after the head reproduces today's prose, so no text is lost or duplicated). Run them: FAIL (module missing).
- [ ] **Step 3: Implement `prompt_areas.py` and rewire `SYSTEM_PROMPT`.** Split today's f-string into a head template (the paragraph before the schema, the schema, and "Facts:"), and `BULLETS` copied verbatim in order (read `planner.py` lines 285-436; keep every backslash line continuation's result, i.e. the joined text, identical). Tag each bullet by the table in the spec's "Areas", reading the bullet's own text for which actions it names. In `planner.py` set `SYSTEM_PROMPT = build_prompt(PlannerOutput.model_json_schema(), None)` and add `scoped_prompt(areas)` later (Task 2). Keep `json` imported only if still used.
- [ ] **Step 4: Run** `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_prompt_areas.py -q`. Expected: all pass. Then run `tests/test_workspace_planner.py` in the background (about 20 minutes) and read the tail: Expected: unchanged results (all pass).
- [ ] **Step 5: Lint and commit** (`ruff check`, `ruff format`, `pyright` on the three files, separate commands; `feat(planner): tag the prompt's bullets with areas`).

### Task 2: Scoped schema and scoped prompt

**Files:**
- Modify: `csa/workspace/prompt_areas.py`, `csa/workspace/planner.py` (add `scoped_prompt`)
- Test: `tests/test_prompt_areas.py`

**Interfaces:**
- Produces:
  - `allowed_actions(areas: frozenset[Area]) -> frozenset[str]`: the `general` actions, the selected areas' actions, and `plan` plus the step actions' shapes when any of bookings, training, inbox or email is selected
  - `build_prompt(schema, areas)` for `areas` not `None`: the same head, the schema filtered to `allowed_actions` (the discriminator mapping, the `oneOf`/union members and only the `$defs` still reachable), and the bullets whose areas intersect `areas | {"general"}`
  - `scoped_prompt(areas: frozenset[Area]) -> str` in `planner.py`, cached per areas set (`functools.cache`)
- Consumes: Task 1.

- [ ] **Step 1: Write the failing tests:** `test_scoped_schema_lists_only_allowed_actions` (for `{"hr"}`: the filtered schema's mapping keys equal `allowed_actions`, `email_compose` is not in the serialized schema, `hr_process_read` is); `test_scoped_schema_keeps_the_defs_it_references` (`plan` selected keeps the step action defs; no dangling `$ref`, checked by resolving every `$ref` in the filtered schema); `test_scoped_prompt_has_each_selected_actions_bullet_and_rules` (for every area alone: each of its actions' bullets and every bullet naming one of its actions is in the prompt; a bullet only another area owns is not); `test_a_one_area_prompt_is_much_smaller` (for each area alone, `len(scoped_prompt({a})) < 0.6 * len(SYSTEM_PROMPT)`; the ceiling is a placeholder, set it from the measured sizes at step 3 and write the measured numbers in the commit message); `test_scoped_prompt_is_valid_json_schema_text` (the embedded schema text parses with `json.loads` after slicing it between the head and "Facts:").
- [ ] **Step 2: Run to verify they fail.** Expected: FAIL (`allowed_actions` missing).
- [ ] **Step 3: Implement** `allowed_actions`, the schema filter (a function `filter_schema(schema, actions)`; deep-copy, prune `$defs` by reachability from the kept union members and `plan`'s steps), and `scoped_prompt`. Print `len(scoped_prompt({a}))` for each area and set the ceiling in the test.
- [ ] **Step 4: Run** `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_prompt_areas.py -q`. Expected: all pass.
- [ ] **Step 5: Lint and commit** (`feat(planner): scoped prompt and schema per area`).

### Task 3: The deterministic selector

**Files:**
- Create: `csa/workspace/area_select.py`, `tests/test_area_select.py`

**Interfaces:**
- Produces:
  - `word_rule_areas(text: str) -> frozenset[Area]`: English and Malay word rules per area, built from the trigger words the existing action bullets use (for example bookings: book, move, cancel, reschedule, free, busy, tempah, batal, kosong; products: stock, inventory, stok; inbox: approve, reject, held reply; hr: onboarding, offboarding, task, tugasan, profile, leave, berhenti; the existing `_MAIL_WORDS`, `_TRAINING_WORDS`, `_PEOPLE_WORDS`, `_TEAM_WORDS`, `_MANAGER_WORDS` from `service.py` are reused by importing them, not copied)
  - `select_areas(message: str, *, tab: str | None, record_kind: str | None, previous: PriorTurn | None, needs: Needs) -> frozenset[Area] | None`: the union of the signals in the spec's phase A list (tab map, word rules, `Needs` map, record-kind map, previous turn's action and message, colleague pick `^Use employee \S+ for: ` keeps email); `None` when the union is empty
- Consumes: `Needs` from `csa/workspace/routing.py`, `PriorTurn` from `csa/workspace/planner.py` (no import of the service: copy nothing, import the compiled regexes from `service.py` only if that creates no cycle; otherwise move them to a small `csa/workspace/words.py` and re-import them in `service.py`, a ruling to ledger).

- [ ] **Step 1: Write the failing tests:** one parametrized table of `(message, tab, record_kind, previous, needs, expected)` rows covering: each tab alone; each area's words in English and in Malay; `Needs(mail=True)` selects email; `Needs(manager=True)` selects email and hr; a record kind alone; a previous turn with a real action keeps its area; a previous `clarification` plus the earlier message's words keeps the earlier area; a colleague pick keeps email; the cross-area "email Aina about her leave" selects email and hr; "hello" with `overview` and no history returns `None`; the always-present bookings fact is not a parameter, so it cannot select bookings.
- [ ] **Step 2: Run to verify they fail.** Expected: FAIL (module missing).
- [ ] **Step 3: Implement** both functions. Over-inclusion is the rule: when a word is shared by two areas, select both.
- [ ] **Step 4: Run** `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_area_select.py -q`. Expected: all pass.
- [ ] **Step 5: Lint and commit** (`feat(planner): deterministic area selector`).

### Task 4: Wiring, the switch and the widening safety net

**Files:**
- Modify: `csa/workspace/planner.py` (`PlannerFacts.areas`, `planner_prompt`, `WorkspacePlanner`), `csa/workspace/service.py` (`planner_facts`, `_corrected_plan`), `csa/core/settings.py`, `deploy/compose.staging-local-proposal.yml`, `scripts/rollout_targets.py`
- Test: `tests/test_workspace_planner.py` (new tests at the end), `tests/test_area_wiring.py`

**Interfaces:**
- Produces:
  - `PlannerFacts.areas: frozenset[Area] | None = Field(default=None, exclude=True)` (never in the model's JSON, like `resume_group`)
  - `WorkspacePlanner.plan(message, facts, *, note=None, widen=False)`: uses `scoped_prompt(facts.areas)` when `facts.areas is not None and not widen`, else `SYSTEM_PROMPT`; `planner_prompt` omits the payload of facts whose area is not selected when scoped (the always-present `bookings` clock goes only with bookings)
  - Settings `WORKSPACE_AREA_PROMPTS: bool = False`; compose variable `CSA_STAGING_WORKSPACE_AREA_PROMPTS` (default false) and the key added to the allowed keys in `scripts/rollout_targets.py`, following how `CSA_STAGING_WORKSPACE_ASSISTANT` is wired (grep it); the key is NOT added to `deploy/*-rollout.env` here
  - `_corrected_plan(planner, message, facts, resolve, *, widen_enabled: bool = True)`: at most two model calls as today; the second is final. It widens (second call with `widen=True`) when the first proposal's action is not in `allowed_actions(facts.areas)`, or when the first call raised `PlannerInvalidOutput` whose reason is not in `CORRECTION_NOTES` and `facts.areas` is not `None`; a content or rule note re-plan keeps the scoped prompt; `widen_enabled=False` (the smoke's `--no-widen`) never widens
  - `planner_facts` sets `areas=select_areas(...)` only when the switch is on (read from `request.app.state` settings, like `workspace_planner`), else `None`
- Consumes: Tasks 1-3.

- [ ] **Step 1: Write the failing tests** in `tests/test_area_wiring.py` with a fake `JsonCompletionClient` that records the `system` it receives and returns scripted `PlannerOutput` JSON: `test_areas_never_appear_in_the_model_facts_json` (`planner_prompt` output has no `areas`); `test_scoped_prompt_is_used_when_areas_are_set` and `test_full_prompt_is_used_when_areas_is_none`; `test_out_of_area_action_gets_one_full_prompt_replan` (areas `{"hr"}`, first output `email_compose`, second `answer`: two calls, the second `system` equals `SYSTEM_PROMPT`, result is the second); `test_invalid_output_with_no_named_reason_widens_once` (first call raises `PlannerInvalidOutput(None)`, second succeeds on the full prompt); `test_widening_is_final` (a second out-of-area output is returned as is, no third call); `test_content_note_replan_keeps_the_scoped_prompt`; `test_widen_disabled_never_widens`; `test_switch_off_makes_no_selection` (settings default false: `planner_facts` leaves `areas` None; run through the same fixtures `tests/test_workspace_planner.py` uses for `planner_facts`, which need Docker, and mark them with `requires_docker` as that file does). Run them: FAIL.
- [ ] **Step 2: Implement** the plumbing with Python replace scripts (see Global Constraints). Add the settings field, compose variable and rollout-targets key.
- [ ] **Step 3: Run** `tests/test_area_wiring.py` and the whole of `tests/test_workspace_planner.py` and `tests/test_assistant_smoke.py` in the background. Expected: all pass (the switch is off, so existing tests see the full prompt).
- [ ] **Step 4: Lint** (`ruff check`, `ruff format`, `pyright`, `lint-imports` from `/home/amsyrzmi/CSAV2/.venv/bin`) and commit (`feat(planner): scoped prompts behind WORKSPACE_AREA_PROMPTS, with a widening safety net`).

### Task 5: Smoke harness: areas, tab, record, `--areas`, `--no-widen`

**Files:**
- Modify: `scripts/assistant_smoke.py`, `tests/test_assistant_smoke.py`

**Interfaces:**
- Produces:
  - `CaseFacts.record: RecordRef | None` and `CaseFacts.record_summary: str | None` (synthetic text, max 500 chars), used by `build_facts` for `page_context.record` and `record_summary`
  - `Expect.areas: list[Area]` (the areas the expected actions need; empty means "derive from `actions`"), `extra="forbid"` stays
  - CLI `--areas` (use the selector: `build_facts` sets `facts.areas = select_areas(...)` with `word_rule_needs(message)`; default off, so a default run is the full-prompt baseline) and `--no-widen` (passes `widen_enabled=False` to `_corrected_plan`); the printed lines gain the word `widened` for a widened sample and `RATE` lines are unchanged so `smoke_compare` still reads them; `_RecordingPlanner.plan` accepts `widen`
- Consumes: Tasks 3 and 4.

- [ ] **Step 1: Write the failing tests** in `tests/test_assistant_smoke.py` (it uses a scripted client): a case with a record and summary reaches the planner's facts; `--areas` sets `facts.areas` and a default run leaves it `None`; `--no-widen` with an out-of-area scripted output yields a failing outcome and one call, without it two calls and a `widened` mark; `Expect.areas` rejects an unknown area; an offline test `test_selector_covers_the_expected_areas_of_every_case`: for every case in `smoke_cases.json`, `select_areas(...)` (with the case's tab, record kind, last history turn and `word_rule_needs`) is `None` or contains the case's areas (`Expect.areas`, else derived from `actions` through `ACTION_AREA`, ignoring `general`), and prints the share of cases with a scoped (non-`None`) prompt, asserted `>= 0.9` only after Task 6 adds the cases.
- [ ] **Step 2: Run to verify they fail.** Expected: FAIL.
- [ ] **Step 3: Implement** the harness changes with Python replace scripts (the editor hook reformats; run project `ruff format` after).
- [ ] **Step 4: Run** `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_assistant_smoke.py -q`. Expected: all pass (the `>= 0.9` assertion is skipped with a clear reason until Task 6).
- [ ] **Step 5: Lint and commit** (`feat(smoke): area selection, record facts and --no-widen`).

### Task 6: New smoke cases and the offline selector gate

**Files:**
- Modify: `csa/workspace/smoke_cases.json`, `tests/test_assistant_smoke.py` (turn the skip into the real `>= 0.9` assertion), `docs/ASSISTANT_RETEST.md` only if its rows list smoke ids (check with grep)

**Interfaces:** Consumes Task 5's `CaseFacts.record`, `Expect.areas`. Produces about 20 new cases (ids `<feature>-<en|ms>-<n>`, unique, synthetic): cross-area turns (email about leave; book a room and email the attendees; a training request mixed with an HR question), follow-ups after a clarification and after a directory pick, an HR message on the email tab and an email message on the hr tab, Malay with English names, an open conversation (inbox decision), an open product (stock read), an open training video (training_ask), a message that matches nothing (expects the full prompt: `Expect.areas: []` and an `answer` or `clarification`), and one case per area whose expected action is `answer` but depends on the area's prose (for example `booking_free`, an HR denial).

- [ ] **Step 1: Write the cases** with `Expect.areas` set explicitly; no real names, addresses or customer text.
- [ ] **Step 2: Run the offline test** `/home/amsyrzmi/CSAV2/.venv/bin/python -m pytest tests/test_assistant_smoke.py -q -k selector`. Expected: it fails for any case where the selector misses an area. Fix the selector's word lists or tab/record maps (Task 3's files, with a test row per fix) until it passes with the share of scoped prompts `>= 0.9`; ledger the cases that needed the full prompt on purpose.
- [ ] **Step 3: Run** the whole `tests/test_assistant_smoke.py` and `tests/test_area_select.py`. Expected: pass.
- [ ] **Step 4: Lint and commit** (`test(smoke): cross-area, follow-up, tab and record cases`).

### Task 7: Measure, tune, record (needs the owner's smoke yes)

**Files:** scratchpad outputs; `docs/PROGRESS.md`; the spec (add "Results of the build"); possibly `csa/workspace/prompt_areas.py` tags and `area_select.py` word lists.

**Interfaces:** Consumes everything above and `scripts/smoke_compare.py` (`python -m scripts.smoke_compare BASELINE CANDIDATE`).

- [ ] **Step 1: Ask the controller for the smoke yes** and the budget above; do not start before it. Copy the run recipe to the scratchpad as `run.sh` and let it pass extra arguments (append `"$EXTRA"` before the case args; the recipe's `--repeat $rep $args` line is where).
- [ ] **Step 2: Baseline** (full run 1): `run.sh <worktree> 5` into `baseline.txt` (default flags: the full prompt, all cases including the new ones). Check 99 + new `RATE` lines and a final `N passed, M failed`.
- [ ] **Step 3: Candidate** (full run 2): `EXTRA="--areas --no-widen" run.sh <worktree> 5` into `candidate1.txt`; run `smoke_compare` and read every DOWN line. Also report the share of scoped prompts (selector test) and per-area prompt sizes. Then one more full run (full run 3) with `EXTRA="--areas"` (widening on, the production behaviour) into `candidate1-widen.txt` and report the widened share per area from the `widened` marks (the gate's 10% ceiling) and any pass that depends on widening.
- [ ] **Step 4: Tune** only through tests-first edits: a failing case is diagnosed by what the model returned (a scratch copy with a temporary print, never committed; print error locations and types, not model text into files), then fixed by an area tag, a word-list entry or one wording change, followed by a targeted run of up to 10 samples of that case and its siblings from the same area and from one other area. At most two more full runs (4 and 5 of 5) for the tuned candidate (`--areas --no-widen`) and a final widen-on read if the tags changed; stop and report to the controller if the gate cannot pass (do not widen the budget).
- [ ] **Step 5: Record**: `docs/PROGRESS.md` top entry and the spec's results section with run numbers (baseline total, candidate total, cases at 5/5, per-case UP/DOWN table from `smoke_compare`, widened share per area, scoped-prompt sizes); case ids and counts only. Commit (`docs: planner area prompts results`) and report the commit list, the final compare output and the lint results to the controller.

### Task 8: Phase B: the decider's area answers

**Files:**
- Modify: `csa/workspace/routing.py`, `csa/workspace/service.py` (`planner_facts`), `docs/superpowers/evals/assistant_jev_labels.json`
- Test: `tests/test_workspace_routing.py` (it has `FakeJev`, `_jev`), `tests/test_area_wiring.py`

**Interfaces:**
- Produces: in `routing.py`, `AREA_QUESTIONS` (one `noul` question per area, keys `needs:area:<area>`, wording like the existing `_DESCRIPTIONS`, English, describing the area's requests); `routing_questions()` returns the five fact questions plus the six area questions in the same request; `route_facts` returns a `RoutedNeeds(needs: Needs, areas: frozenset[Area])` (a frozen dataclass) built as the decided answer OR the word-rule fallback per question, so the decider only adds; `planner_facts` merges `areas` into the selector's result (`select_areas(...)` union `routed.areas`; `None` only if both are empty) when the switch is on. No new decider mode, no new request, no new setting.
- Consumes: Tasks 3-4.

- [ ] **Step 1: Write the failing tests:** `route_facts` with a scripted decider that answers yes to `needs:area:hr` for a message the word rules leave empty returns that area; a decider that answers no never removes a word-rule area; a decider error falls back to the word rules (the existing tests' pattern); the request holds exactly 11 questions in one call; `planner_facts` merges the two (a Docker test marked like its neighbours); the labels file validates against `scripts/assistant_jev_eval.py`'s loader (run its label-loading function in a test, no key needed).
- [ ] **Step 2: Run to verify they fail.** Expected: FAIL.
- [ ] **Step 3: Implement** the routing change and extend the labels: add `needs:area:*` expectations to every existing routing label and about 24 new English and Malay area labels (synthetic ids `routing-en-NN`, `routing-ms-NN`; messages at example domains only).
- [ ] **Step 4: Run** `tests/test_workspace_routing.py tests/test_area_wiring.py tests/test_area_select.py -q` (Docker ones in the background). Expected: pass.
- [ ] **Step 5: Lint and commit** (`feat(routing): the decider answers the area questions in the same request`). Tell the controller the owner must run `scripts/assistant_jev_eval.py` before routing goes to `cascade`.

---

## Self-review (done)

- **Spec coverage:** areas and tags (T1), scoped schema/prompt (T2), selector with all seven signals (T3, with the `Needs` map and record kind), wiring, switch, widening incl. the invalid-output rule and `--no-widen` (T4, T5), new cases and offline recall (T6), gate and budget (T7), phase B (T8). Rollout (switch on for staging, production, routing to `cascade`) is the controller's separate pull requests after the owner's yes, outside these tasks.
- **Rulings against the spec:** the existing prompt assertions stay on the full prompt (identity makes moving them churn); the gate's "ceiling 10% widened" is reported by the smoke's `widened` mark, which is only meaningful on runs without `--no-widen`: Task 7 therefore also runs one candidate pass without `--no-widen` to read the widened share.
- **Type consistency:** `Area`, `ACTION_AREA`, `BULLETS`, `build_prompt`, `allowed_actions`, `scoped_prompt`, `select_areas`, `word_rule_areas`, `PlannerFacts.areas`, `widen`, `widen_enabled`, `RoutedNeeds` are named the same in every task that uses them.
- **Proportion:** bodies are not written out; steps give signatures, test names and assertions.
