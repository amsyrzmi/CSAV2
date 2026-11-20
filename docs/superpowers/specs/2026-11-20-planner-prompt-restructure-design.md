# Planner prompt restructure: a compact action catalogue generated from the contract

Status: design for the owner's approval. Nothing is built or deployed.

## Why

The workspace assistant's planner (`csa/workspace/planner.py`, `SYSTEM_PROMPT`) is one model call
with a prompt of 36,402 characters. 21,881 of them are the JSON schema of `PlannerOutput`
(`json.dumps(PlannerOutput.model_json_schema())`, with every nested `$defs` entry); 14,521 are prose
(facts, actions, rules). Each new assistant area (HR 1a, 1b, 1c-i, 1c-ii so far; attendance,
recruitment, leave and claims, payroll next) adds a contract class, so the schema half grows with
every slice.

Evidence that the size now costs us (all from the smoke file `csa/workspace/smoke_cases.json` on
staging):

- 1c-i: adding one action made two Malay cases fail until prompt wording was changed.
- 1c-ii: the first run was 92/99; one added sentence fixed the three positives, but rewording the
  approval rule dropped `hr_process_neg-en-5` from 5/5 to 1/5. Wording tweaks in a long prompt move
  unrelated cases.
- Open on production now: `hr_process_neg-ms-5` 0/5 (harmless: the card only offers the caller's own
  step), `hr_process-ms-1` 0 to 1 of 5, and flaky `hr_process-en-4`, `en-5`, `ms-5`,
  `email_batch_rest-ms-1`.

Every call also pays for the whole prompt (input tokens and latency), about 400 to 560 ms per call
today.

## Goals

- Cut the prompt by about 40% without changing what the planner may output (the contract, the
  executors and the server-side validation stay as they are).
- Make the catalogue **generated** from the contract classes, so a new action adds one line to the
  prompt by construction and the prompt cannot drift from the validator.
- Make every future prompt change measurable with one repeatable gate (the smoke file at 5 samples
  per case, compared case by case with a baseline).
- Keep the current pass rate and fix the open Malay HR reads where the compaction already does.

## Non-goals and anti-goals

- No second model call, no router, no area-scoped prompts in this project (see Approaches).
- No change to the contract classes' fields, the executors, confirm flow, migrations or the console.
- No model, temperature, `reasoning_effort` or token-limit change.
- Anti-goal: shipping a prompt that is smaller but has any case worse than the baseline without an
  owner-visible reason.
- Anti-goal: the compact form hiding a rule that only the schema or a docstring carried.

## Measured so far (throwaway experiment, nothing committed)

Whole smoke file, 99 cases, 5 samples each (495 samples), staging gateway image in a throwaway
container, synthetic data, the owner's model key.

| Prompt | Chars | Samples passing | Cases at 5/5 |
|---|---|---|---|
| Current | 36,402 | 475 / 495 | 92 |
| A: compact catalogue, docstrings dropped | 18,442 | 458 / 495 | not counted |
| B: compact catalogue, contract docstrings kept | 21,889 | 474 / 495 | 94 |

- **Docstrings are load-bearing.** Variant A dropped the pydantic docstrings, which become schema
  `description` text. Rules such as "a group recipient needs `group`" (`RecipientSpec`) live only
  there. Without them: email_batch group, plan_bookings and others produced invalid contracts
  (12 invalid-output samples). So the generator must carry docstrings.
- **B fixes:** `hr_process-ms-1` (0/5 to 5/5), `hr_process-ms-3`, `hr_process-en-5`,
  `hr_process_neg-en-5`, `email_batch_rest-ms-1`.
- **B regresses (each 5/5 to 0/5):**
  - `attention-en-1`: output is an invalid contract where `inbox_summary` was expected. Cause not
    yet found.
  - `directory-ms-3` ("hantar emel kepada danial"): the model returns a clarification whose options
    are the placeholders `directory_lookup` and `type_address`, which fails `no_placeholder_options`.
  - `email_batch_group-ms-1`: right action, but it sets `mode:"shared"` instead of `"personalised"`.
- **Unchanged:** `hr_process_neg-ms-5` stays 0/5 in both prompts.
- Reading: layout alone moves near-boundary cases across the line in both directions. Size is not
  the whole cause, so the gate has to be case-level, not a total.

## Approaches considered

1. **Compact catalogue generated from the contract, one call (chosen).** The schema half is replaced
   by a generated catalogue: one line per action with its fields (`?` for optional, enums inline,
   nested models expanded), each action's docstring under it, plus the notes of the nested models
   that carry rules (`RecipientSpec`, `BookingFreeAsk`, `RecordRef`, `ClarificationOption`). Prose
   rules stay. Cheaper on every call; regressions get targeted wording fixes. Trade-off: the three
   regressions above must be fixed or explained before it ships.
2. **Option 1 plus area-scoped prompts chosen server-side** (an HR turn sees only HR and general
   actions). Isolates future HR growth, but a wrongly chosen area becomes a new failure mode and the
   routing facts (`route_facts`, word rules) are not a reliable area signal. Deferred until option 1
   is in and measured.
3. **A router model call before the planner.** Adds about 0.4 s and a model's cost to every message
   plus a new failure mode, and the evidence says size is not the only cause. Not recommended. It
   needs an explicit owner decision if ever revisited.

The owner chose option 1 on 2026-11-20.

## Design

### Parts

1. `csa/workspace/planner_catalogue.py` (new): `compact_catalogue(schema: dict) -> str`, a pure
   function from `PlannerOutput.model_json_schema()` to the catalogue text. It renders `$ref`
   inline, `const`, `enum`, `anyOf` with null removed, arrays, `YYYY-MM-DD` dates, objects as
   `{field?: type}`, one line per action from the discriminator mapping with its whitespace-collapsed
   docstring below, then the nested-model notes. It fails loudly (raises) on a schema node it does
   not know, so a new pydantic construct cannot be silently dropped from the prompt.
2. `csa/workspace/planner.py`: `SYSTEM_PROMPT` uses `compact_catalogue(...)` instead of the
   `json.dumps` of the schema. Prose is touched only where a measured regression needs it.
3. A compare script (`scripts/smoke_compare.py` or an option of `assistant_smoke`): reads two
   smoke outputs at the same `--repeat` and prints, per case, baseline vs candidate pass count,
   and exits non-zero if any case is worse than the baseline by more than the allowed margin.
   `assistant_smoke` already has `--repeat N` and `--case ID`; its FAIL/RATE lines are the input.
4. Tests (unit, no model): the catalogue contains every action in the discriminator mapping and
   every field of each contract class (a drift test against the pydantic models); the docstring of
   each action and the nested-model notes appear; an unknown schema node raises; the generated
   prompt is smaller than a stated ceiling; the example contract objects in the existing planner
   tests still validate (the contract itself is unchanged).

### The gate (what "no worse" means)

Run the whole smoke file at `--repeat 5` on the baseline (current `main`) and on the candidate, the
same day, the same model. Ship only if every case's candidate count is at least the baseline count
minus 1, no case that is 5/5 on the baseline drops below 4/5, and the total is at least the
baseline total. Cases that are fixed (up) are listed in the PR. A single flaky case that passes on
a rerun of that case alone at `--repeat 10` is noted, not blocking. The numbers go in the PR
description and in `docs/PROGRESS.md`.

The three known regressions are fixed by wording that stays inside the catalogue's budget (for
example a one-line placeholder-options note next to `clarification`, a `mode` reminder next to
`email_batch`, and whatever the `attention-en-1` diagnosis shows). If one cannot be fixed without
making another case worse, the PR says so and the owner decides; the prompt is not shipped with a
silent regression.

### Rollout

Normal path: PR, green CI, review, merge, automatic staging deploy, the whole smoke on staging after
the owner's yes (it uses the owner's model key), then production through the `deploy-production`
workflow with the owner's approval click. No migration, no data change, no contract change; a
rollback is a normal rollout of the previous commit.

## Constraints

- The repo is public: smoke cases are synthetic; no secrets or real addresses in the doc or tests.
- The smoke run needs the owner's yes before it starts.
- Pydantic docstrings on contract classes stay load-bearing; changing one is a prompt change and
  goes through the gate.
- `PlannerOutput` validation on the server stays the only authority on what is accepted.

## Left to the builder

- Exact wording of the three regression fixes and the final catalogue notation, as long as the
  gate passes.
- Whether the compare script is a new file or an option of `assistant_smoke`.
- The size ceiling in the drift test (suggest 24,000 characters for the whole `SYSTEM_PROMPT`;
  variant B is 21,889).
- Diagnosis of `attention-en-1` (print the raw model output; find which field breaks the contract).

## Risks

- Smoke is 5 samples per case at temperature 0 with a hosted model: a pass count of 4/5 vs 5/5 is
  noise-sized. The gate tolerates a one-sample drop and treats a 5/5 to 3/5 or worse as real.
- The hosted model can change under us; a baseline taken on another day is not comparable, which is
  why baseline and candidate run the same day.
- `hr_process_neg-ms-5` stays failing either way; it is not made worse and is tracked separately.

## Starting point and how a candidate is measured

- **Variant B survives.** Its generator is kept beside this doc as
  `2026-11-20-planner-prompt-restructure-assets/variant_b_compact.py.txt` (55 lines, throwaway
  quality). The build starts from it: first reproduce B's result (474/495 with the three known
  regressions) with the real module, then fix the three. The only change to `planner.py` in the
  experiment was importing the generator and putting its output in place of the `json.dumps` of the
  schema.
- **How a run is done.** `throwaway_smoke_run.sh.txt` (same folder) runs a worktree's `csa/` and
  `scripts/` over the current staging gateway image in a throwaway container, joined to the staging
  gateway's network and volumes with its environment passed through without printing it. Usage:
  `<script> <worktree> <repeat> [case...]`; it prints only the FAIL, RATE and summary lines. No
  image build is needed. The script reads the gateway's environment (including the model key) but
  never echoes it.
- **Who runs it:** the builder, in that container, only inside the smoke budget below.

## Smoke budget (the owner's call)

A full run is 99 cases x 5 samples = 495 model calls on the owner's key. The proposed budget, for
one yes covering the whole project: up to 4 full runs at `--repeat 5` (the baseline, the first
reproduction of B, and two candidates) plus targeted `--case` runs of up to 10 samples each for the
regression fixes, and one final full run on staging after merge. A fifth full run needs a new yes.

## Results of the build (2026-11-20)

Whole smoke file, 99 cases at 5 samples, the same day, staging image, throwaway container. The four
full runs of the budget:

| Run | Prompt | Passing | Gate |
|---|---|---|---|
| 1 | current (JSON schema), baseline | 475 / 495 | reference |
| 2 | generated catalogue, as variant B | 471 / 495 | 5 cases down |
| 3 | + clarification wording, + `personalised` first | 469 / 495 | 4 cases down |
| 4 | variant B + "no other fields" + `personalised` first | **479 / 495** | 2 cases down |

Run 4 is the committed candidate (prompt 21,889 characters against 36,402). Against the baseline:
up `email_batch-en-1` 2 to 5, `email_batch_rest-en-1` 2 to 5, `hr_process-en-5` 0 to 5,
`hr_process-ms-1` 1 to 2; down `email_batch_rest-ms-1` 5 to 1, `hr_process_neg-en-5` 5 to 2,
`plan_bookings-en-1` 5 to 4 (inside the tolerance). Two violations of the gate remain.

### Fixed, with the cause

- `attention-en-1` (5/5 to 0/5 in variant B): the catalogue wrote the no-field action `inbox_summary`
  as `{}`; the model then wrote `{"action":"inbox_summary","}` (a malformed-JSON 400 from the
  provider). A no-field action is now rendered as `no other fields`: 10/10.
- `email_batch_group-ms-1`: the `shared` clause came first in the `email_batch` bullet; putting the
  `personalised` rule first gives 10/10.
- `directory-ms-3` came back to 10/10 without any wording change of its own (a layout shift).

### Tried and not kept (targeted runs of 10 samples, sensitive-case battery of 16 cases)

| Change | What it did |
|---|---|
| Clarification options "never a placeholder" | fixed `directory-ms-3`, broke `hr_process-ms-4` (0/10) |
| "never a card or a clarification" in the approval rule | `hr_process_neg-en-5` 8/10, `hr_process-ms-4` 0/10 |
| "(never an HR process task)" on `inbox_decision` | no effect on `neg-en-5` |
| Render length and range limits (`(1-40 items)`, `(1-200 chars)`) | `email_batch_rest-ms-1` 9/10 (the model had written `recipients: []` and `subject: ""`, which the contract refuses), but `hr_process-en-4`, `ms-4`, `neg-en-5` to 0/10 |
| Limits + "reads is only for ..." | HR reads back to 10/10; `email_batch_rest-ms-1` 0/10, `-en-1` 5/10 |
| + carry-over wording for "the rest" | `hr_process-ms-1` 10/10, `neg-en-5` 9/10, `rest-ms-1` 7/10, but `training-ms-3` 1/10, `hr_process-ms-3` 4/10 |
| Variant B + carry-over wording only | battery 124 / 160 against 132 for B |

### What this says

- Every change moved a different set of cases. The cases that flipped were in a different area from
  the change (an email wording changed Malay HR reads and a training case). The single prompt couples
  all areas: a fix in one area cannot be made without risking the others.
- Some cases are plain noise: the identical run-4 prompt scored `hr_process_neg-en-5` 1/10, 9/10 and
  2/5 within hours (the hosted model is not deterministic at temperature 0).
- `email_batch_rest-ms-1` is not noise: it sits at about 20 to 40% on the compact prompt in every
  measurement (the old prompt gave 5/5).
- The generator is correct and worth keeping (it also carries the contract's docstrings and raises on
  a construct it does not know); the gate script made each trial cheap and comparable.

### Consequence for the approaches

The evidence favours approach 2 of this document (area-scoped prompts chosen server-side) over more
wording work on one prompt: with an HR turn seeing only HR and general actions, a change to email or
training wording cannot flip an HR case. That needs its own design (how the area is chosen, what
happens on a wrong choice) and is not part of this project.
