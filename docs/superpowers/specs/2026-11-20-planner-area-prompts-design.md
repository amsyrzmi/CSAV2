# Planner area prompts: each turn sees only the areas it needs

Status: phase A built and measured behind a switch that is off by default; phase B (the decider's area
questions) is not built. See "Results of the build" at the end.

## Why

The workspace assistant's planner (`csa/workspace/planner.py`) is one model call whose system prompt
is 36,402 characters: about 21,900 of JSON schema for every action, and about 14,300 of prose (facts,
one bullet per action, rules). Every area (email, inbox, training, HR, bookings, products) is in every
call, and more HR is coming (attendance, recruitment, leave, claims, payroll).

PR #170 (`docs/superpowers/specs/2026-11-20-planner-prompt-restructure-design.md`, "Results of the
build") measured what that costs. A prompt 40% smaller scored 479/495 against 475/495, but any wording
change moved cases in a different area: an email sentence flipped Malay HR reads and a training case.
One prompt couples all areas, so a fix in one area cannot be made without risking the others. The same
model also gives different answers to the identical prompt (a case scored 1/10, 9/10 and 2/5 within
hours), so every claim here is measured the same day against a same-day baseline.

## Goal

A turn's prompt contains the general part plus only the areas the turn needs, so (a) an edit to one
area's text cannot change what the model does in another area, (b) a one-area prompt is far smaller
(an estimate to be measured: about a third of today's), and (c) a new HR slice adds text only to HR.

## Non-goals and anti-goals

- No new model call per turn. The deterministic selector is server code; the decider (below) answers inside the routing request the service already sends, never in a request of its own.
- No change to the contract (`PlannerOutput` still validates the full action union), the executors,
  the confirm flow, migrations or the console. No change of model, temperature or token limits.
- No change of notation in the first build: the JSON schema stays the notation, filtered to the
  selected actions. (The compact catalogue from #170 stays available for a later step.)
- Anti-goal: a turn that needs an action whose area was not selected and gets a wrong answer without
  the server noticing. The design below bounds this; the gate measures it.
- Anti-goal: shipping with any case worse than the same-day baseline without the owner seeing it.

## Areas (the proposed map; the builder completes the per-bullet assignment)

| Area | Actions | Prose today (approx.) |
|---|---|---|
| general (always) | answer, navigate, clarification | header, history, record_summary, page_hint, the closing rules (facts are trusted, correction block, language, JSON only, never invent ids): about 2,500 |
| email | directory_lookup, email_compose, email_reply, email_team, email_batch, inbox_summary | mailbox_threads, my_team, my_manager, recipients and send rules, email_batch: about 3,000 |
| inbox | inbox_decision | about 200 plus the send rule |
| training | training_submit, training_ask | my_employee and the two bullets: about 1,300 |
| hr | hr_person_add, hr_person_update, hr_person_leave, hr_person_open, people_query, hr_process_read, hr_task_complete | people_access, my_manager, five HR rules and seven bullets: about 3,300 |
| bookings | booking_action, plan | bookings fact, bookings rules: about 2,000 |
| products | product_stock_read | about 700 |

A prose bullet or rule may belong to more than one area (the send rule mentions email, training and
inbox actions); it is included when any of its areas is selected. `plan` has steps that are
`booking_action`, `training_submit`, `inbox_decision` or `email_batch`: it is included with bookings,
and with any turn that selects two or more of bookings, training, inbox and email; the steps' action
shapes come with it through the schema filter (reachable definitions).

Fact bullets ("present when ...") are included when the fact is present in `PlannerFacts`, not when an
area is guessed: the fact is exactly what the server already decided to read.

## The selector

Two layers: a deterministic selector (phase A) and the routing decider's answers added to it (phase B). The
result is always the union, so the decider can add an area and never remove one, exactly as for
`Needs` in `csa/workspace/routing.py`.

### Phase A: deterministic

`select_areas(message, page_context, previous_turn, needs, facts) -> frozenset[Area] | None`, a pure
function. Signals, unioned (it errs on including more):

1. The open console tab (`page_context.tab`): `hr` to hr, `email` to email, `inbox` to inbox and email,
   `bookings` to bookings, `training_library` to training, `business` to products; other tabs add
   nothing.
2. Word rules in English and Malay per area, built from the same trigger words the action bullets
   already use, and extending `word_rule_needs` (mail, team, training, people, manager) in
   `csa/workspace/service.py`.
3. The previous turn's action (`history[-1].action`), so follow-ups ("the rest", a picked person, an
   answer to a clarification) stay in the area of the turn they continue.
4. A colleague pick ("Use employee ... for: ...") keeps email.
5. `Needs` (the fact reads the service already decided from the word rules and the routing decider): `mail` and `team` select email, `training` selects training, `people` selects hr, `manager` selects email and hr. The always-present `bookings` fact does not select bookings.
6. The open record's kind (`RecordRef.kind`): conversation and approval select inbox, order and product select products, employee selects hr, training_request and training_item select training, draft selects email.
7. Follow-ups: the previous turn's action when it carries an area; because the most common follow-up is stored as `clarification`, the word rules also run over the previous turn's message.

If no signal fires, the selector returns None and the full prompt (today's) is used. A scoped prompt
is general plus the union of the selected areas. A selected set of two or more areas is allowed and
normal (the cross-area case "email Aina about her leave" selects email and hr). Fact payloads and
fact bullets follow the selected areas too. The always-present bookings clock is the exception: it is the model's only date, so it is sent whenever any area is selected (only the prose is scoped, so a relative date on an HR turn still knows today).

### Phase B: the routing decider (Jev), in this project

How it is wired today: `route_facts` sends Jev one request with five yes/no questions (manager, mail, team, training, people) through `Decider.decide`, mode set per area by `ASSISTANT_DECIDER_ROUTING`: `llm` (Jev not called), `shadow` (the word rules decide, Jev runs beside and both answers are logged; production is here), `cascade` (Jev decides each question at or above its confidence floor `ASSISTANT_JEV_FLOOR_NOUL`, the word rules otherwise). A Jev error or the 2 s timeout falls back to the word rules.

Phase B adds one yes/no question per area (`needs:area:email`, `inbox`, `training`, `hr`, `bookings`, `products`) to that same request, and the result is the deterministic set plus every area Jev answers yes to. So: no extra request or call per turn (in `llm` mode there is no call at all; in `shadow` and `cascade` the call already happens), and nothing changes when Jev is down. `route_facts` returns the fact needs and the area needs from one request.

What it needs, and who does it: (a) the labels for the Jev evaluation (`docs/superpowers/evals/assistant_jev_labels.json`, English and Malay) gain area questions, and the owner runs `scripts/assistant_jev_eval.py` with their own key (hidden prompt) to see agreement per area question; (b) while production routing stays in `shadow`, the area answers are only logged next to the deterministic ones (typed answers, no message text), which gives a real-traffic comparison for free; (c) the decider's answers change a prompt only in `cascade`, so turning routing to `cascade` on staging and then production is a separate pull request to `deploy/*-rollout.env`, made after the evaluation meets its target and the owner says so.

The planner smoke harness has no decider (it uses the word rules), so phase B is not measured by the planner gate; it is measured by the Jev evaluation and the shadow log.

## Safety net: out-of-area output

`PlannerOutput` still validates the full union, so a model that names an action outside the selected
areas returns a valid contract. The server checks `proposal.action` against the selected areas'
actions; if it is outside, the turn's one allowed re-plan (`_corrected_plan`, at most two model calls
per turn today) runs with the full prompt and is final. This costs a second call only on that failure.
The same widening applies when the first call raises a `PlannerInvalidOutput` with no named reason
while the prompt was scoped (today that is a 502; a scoped prompt that produces a broken contract, as
the malformed-JSON case in #170 did, gets the full prompt once). A re-plan for a content or rule note
keeps the scoped prompt. The second call is final in every case, as today.
A wrong answer that stays inside the selected actions (for example `clarification` where an
unselected area's action was right) is not detectable; the over-inclusive selector and the gate's
cross-area cases are what bound it.

## Rollout

The scoped prompt is behind a switch, default off in code, wired like the other assistant switches
(`deploy/*-rollout.env`). Order: build and measure with the switch off; after the gate passes, a
separate PR turns it on for staging; production follows through `deploy-production` after the owner's
approval click. A rollback is the switch back to off (the full prompt is unchanged and always there).

## Measuring (the gate)

- New smoke cases first, because every one of the 99 cases is single-area today: cross-area turns,
  follow-ups after a clarification, an HR message on the email tab and the reverse, Malay mixed with
  English names, and an unmatched message (fail-open). They run on the old prompt first, so the
  baseline includes them.
- Offline, no model: for every smoke case the selector returns a set that includes the area of each
  expected action, or None. A second offline test reports how many cases get a scoped prompt (target:
  at least 90%) and that no area is selected for a case that expects an action of another area only.
- The smoke harness gets a `--no-widen` option used for gate runs, so a selector miss is not hidden by the rescuing second call; the harness already marks a re-planned sample, and the PR reports the share of samples that widened per area (a ceiling of 10% fails the gate; unit tests with a fake client cover the widening itself).
- Model gate: `scripts/smoke_compare.py` (#170) between a same-day baseline and the candidate, whole
  file at `--repeat 5`: no case falls by more than one sample, a case that was 5/5 stays at 4/5 or
  better, the total does not fall. A violating case is rerun alone at `--repeat 10`; a case that
  passes there is noise and is noted, not blocking. Run counts and numbers go in the PR.
- Smoke budget proposed: up to 5 full runs at `--repeat 5` (baseline, then candidates) plus targeted
  `--case` runs of up to 10 samples, and one final staging run after the switch is on. A sixth full
  run needs a new yes from the owner. Every smoke run needs the owner's yes, which this design asks
  for once, with the budget.

## Tests that move

About 43 assertions in `tests/test_workspace_planner.py` read the full `SYSTEM_PROMPT` for prose
bullets. They move to the builders (a bullet is asserted in the area that owns it), and a new test
fails when an action, fact bullet or rule has no area (a new HR action cannot be forgotten).
`SYSTEM_PROMPT` itself stays: it is the full prompt, the fail-open case and the baseline.

## Approaches considered

1. **Deterministic area selector, filtered JSON schema, one call (chosen, by the owner).** Described
   above: no extra latency, testable offline, rolls back with a switch, one variable changed.
2. **Selector assisted by the routing decider (Jev).** Chosen as phase B on the owner's instruction (2026-11-20), after the deterministic phase A: the decider's area answers ride the existing routing request, add areas and never remove one. It is not live on production until routing is turned to `cascade` by a separate pull request.
3. **Restructure HR only first.** The owner chose to restructure every area in this round.

## Decisions I made after the builder check (the owner can change any)

- `plan` is included with any of bookings, training, inbox or email (it is short and a single-area multi-change request is valid).
- The smoke harness and case schema are extended inside this project: a per-case expected-areas annotation (the offline test is trivial for the 37 cases that expect only answer, navigate or clarification), page tab and open-record facts so inbox, products and open-video cases exist (today 96 of 99 cases have no tab and none expect `inbox_decision` or `product_stock_read`), and the cross-area and follow-up cases. This is the largest part of the test work.
- Gate counts a pass-after-widening as a pass but reports the widened share (ceiling 10%).
- The build assumes the planner and `contracts.py` can change on main while it runs (other work adds actions): the area-completeness test fails on any unassigned action, and the baseline is retaken on the day of each gate run. The owner can tell me if a planner change is known to be coming.

## Left to the builder

- The per-bullet and per-rule area assignment, the Malay and English word lists per area (derived from
  the trigger words in the existing bullets, and validated by the offline selector test), and the
  schema filter (keep only reachable definitions, rewrite the discriminator and union).
- The switch's name and exact wiring, following `ASSISTANT_DECIDER_*`.
- The exact size ceilings per scoped prompt in the tests, after measuring.

## Risks

- Two-area and follow-up turns are the realistic failure: the selector's recall matters more than its
  precision, and the new cross-area smoke cases exist for that reason.
- Model noise at temperature 0: baselines and candidates run the same day; a lone flaky case is rerun
  at `--repeat 10` before it blocks.
- A scoped prompt is a different prompt from the full one: the area prose must be complete enough that
  an action never depends on a bullet that lives in another area (the area-map test checks each
  action's own bullet and the rules that name it are in its area).

## Results of the build (2026-11-20)

### What was built (phase A)

- Every prompt bullet, rule and action has an area (`csa/workspace/prompt_areas.py`); `scoped_prompt(areas)`
  in `csa/workspace/planner.py` builds the general part plus the selected areas' prose and a schema
  filtered to the reachable definitions. `SYSTEM_PROMPT` is unchanged and remains the full prompt, the
  fail-open case and the baseline.
- `select_areas` in `csa/workspace/area_select.py`: the seven signals of the design (tab, record kind,
  word rules in English and Malay, previous action and previous message, colleague pick, `Needs`), the
  union of all, `None` when none fires.
- The switch `WORKSPACE_AREA_PROMPTS` (`csa/core/settings.py`), **off by default**; no
  `deploy/*-rollout.env` was changed.
- The safety net: an action outside the selected areas, or an invalid output with no named reason on a
  scoped prompt, re-plans once with the full prompt (final). A `resolve` refusal also widens (outside
  the gate; a ruling).
- The smoke harness: `--areas`, `--no-widen`, page tab and open-record facts, per-case expected areas,
  26 new cases (cross-area, follow-up, tab, record, unmatched), a Business preset for the product cases.
  The file now holds 125 cases (99 old + 26 new).
- Offline (no model): 0 selector misses over all 125 cases; `tests/test_area_select.py` and
  `tests/test_assistant_smoke.py`: 186 passed.

Phase B (the routing decider's area questions) is not built and is not measured by this gate.

### Runs

Whole smoke file, 125 cases at 5 samples = 625 per full run, all on 2026-11-20, real model
gpt-oss-120b, throwaway container over the staging image. The raw outputs (ids and counts only) are in
`docs/superpowers/specs/2026-11-20-planner-area-prompts-assets/`.

| Run | Flags, commit | Time | Passing | Gate |
|---|---|---|---|---|
| 1 baseline | default (full prompt), `463e4527` | 13:16-13:25 | 602 / 625 | reference |
| 2 candidate1 | `--areas --no-widen`, `463e4527` | 13:26-13:38 | 605 / 625 | 3 violations |
| 3 candidate2 | `--areas --no-widen`, `36810e8e` | 13:50-14:02 | 613 / 625 | 1 violation |
| 4 candidate3_widen | `--areas` (widening on), `df4a01f8` | 14:04-14:14 | 613 / 625 | 2 violations, noise (below) |
| 5 candidate3 | `--areas --no-widen`, `df4a01f8` | 14:14-14:25 | **615 / 625** | **passes** |

Baseline cases below 5/5: `email_batch-en-1` 4, `email_batch_rest-en-1` 4, `email_batch_rest-ms-1` 1,
`hr_process-ms-1` 0, `hr_process-ms-3` 4, `hr_process-en-4` 3, `hr_process-en-5` 2,
`hr_process_neg-ms-5` 0, `follow_up-ms-1` 4.

### Run 2 and its three violations

`hr_process-ms-3` 4 to 2, `hr_process_neg-en-5` 5 to 0, `product_stock-en-1` 5 to 3. Rerun of the three
alone at `--repeat 10` (`rerun_base.txt` against `rerun_cand.txt`, 30 samples each; the rerun's commit,
`463e4527`, is inferred from timing, since the pair ran after run 2 and before `36810e8e` at 13:50;
`rerun_base.txt` is the full prompt, so the commit does not affect it): `hr_process-ms-3`
10/10 against 5/10, `hr_process_neg-en-5` 10/10 against 0/10, `product_stock-en-1` 10/10 against 8/10
(30/30 against 13/30). All three are real, not noise.

### Diagnosis and tuning

Forced-area experiments on the real model (a scratch copy of the code that forced the selected areas;
never committed; 5 to 10 samples each) show which area sets pass and which fail. What the failing sets
had in common, with the likely reason (a hypothesis consistent with the outputs, not a proven cause):

- `hr_process_neg-en-5`, "Please approve Aina's laptop task": `{hr}` 5/5, `{hr, inbox}` 0/5,
  `{hr, inbox, email}` 0/5, `{hr, inbox, training, bookings}` 1/5, all six areas through the scoped path
  5/5. The bare word `approve` pulled inbox into the selection, and the failing outputs
  were `hr_task_complete`; the likely reason is that the inbox prompt's approval wording made that action
  look right.
- `hr_process-ms-3`, "Siapa dalam onboarding sekarang?": `{hr}` 9/10 (and 5/5), `{email, hr}` 6/10 (and
  3/5), `{hr, training}` 2/5. The failing outputs were answers listing names that are in the facts, not calls of
  `hr_process_read`; the sets that failed all added a second area to `{hr}`, so the likely reason is
  that the added email area made answering from the facts look sufficient.
- `product_stock-en-1`: `{products}` alone 7-8/10 over four runs (the failing outputs: an answer "let me
  check" with reads `[read_current_record]` instead of `product_stock_read`); products plus any one of
  bookings, email, training, hr or inbox 10/10 each (50 samples).

Two tuning commits followed:

- `36810e8e`: the weak approve/reject words are suppressed when an HR task word is present; "who is /
  siapa" is a weak signal that adds email and hr only when no other area word matched.
- `df4a01f8`: a selection of exactly `{products}` also gets `inbox` (the smallest companion).

Run 3 (after `36810e8e`) left one violation, `product_stock-en-1` 5 to 2; run 5 (after `df4a01f8`)
left none.

### Final compare (run 5 against run 1)

`python -m scripts.smoke_compare baseline.txt candidate3.txt` prints no `VIOLATION`: the gate passes.

```
DOWN draft-ms-1 5/5 to 4/5
UP email_batch-en-1 4/5 to 5/5
DOWN email_batch_rest-en-1 4/5 to 3/5
UP email_batch_rest-ms-1 1/5 to 5/5
UP follow_up-ms-1 4/5 to 5/5
UP hr_process-en-4 3/5 to 5/5
UP hr_process-en-5 2/5 to 5/5
UP hr_process-ms-1 0/5 to 5/5
UP hr_process-ms-3 4/5 to 5/5
DOWN hr_process-ms-5 5/5 to 4/5
DOWN training_ask-ms-1 5/5 to 4/5
total 602/625 to 615/625
```

Four cases are down by one (allowed): `draft-ms-1`, `email_batch_rest-en-1`, `hr_process-ms-5`,
`training_ask-ms-1`. Seven are up. `hr_process_neg-ms-5` stays at 0/5 in both runs (a known open case
from the HR work, not caused by this project).

### Widening (run 4)

Run 4 ran with widening on: **0 of 625 samples widened (0%; the ceiling is 10%)**. No pass depends on
widening. Source of the count: the committed `candidate3_widen.txt` holds only FAIL lines, RATE lines and
the total (PASS lines were filtered out when the output was captured), so the count cannot be audited from
the file. It was read from the full output at run time: the run's filter kept any line with the word
`widened`, and none appeared. No FAIL line in the committed file carries `widened`. Its compare against the baseline prints two violations, `email_batch-ms-1` 5 to 3 and
`hr_process-en-1` 5 to 3, although nothing was widened, so its prompts equal run 5's, where both cases
score 5/5. That is run-to-run noise of the same configuration (about 2 samples either way at repeat
5), not a regression. The gate decision rests on run 5.

### Prompt sizes (characters; full prompt 36,402)

Scoped prompts: email 20,599; hr 16,088; training 15,922; bookings 15,337; inbox 13,424; products 7,871
(products always travels with inbox, so 15,058). Among the 125 cases, two-area sets run from 15,058
(inbox, products) to 29,963 (email, hr); the three-area set bookings, inbox, products is 17,225.

Over the 125 smoke cases under the final selector (a case with no signal keeps the full 36,402):

| | Characters |
|---|---|
| Mean | 20,236 (44.4% below the full prompt) |
| Median | 16,088 (55.8% below) |
| Mean over the 117 scoped cases | 19,130 |
| Range of scoped prompts | 13,424 to 29,963 |

117 of 125 cases (93.6%) get a scoped prompt (floor 90%). Eight keep the full prompt on purpose:
`navigate-en-3`, `navigate-ms-4`, `navigate-en-4`, `navigate-ms-5`, `answer-en-1`, `answer-ms-1`,
`no_area-en-1`, `no_area-ms-1`. Precision (report only): 0.44 extra areas per scoped case. The smoke
cases are not production traffic, so the real mean depends on the mix of messages.

### Budget

5 of 5 approved full runs used, plus one 3-case rerun pair at 10 samples and about ten forced-area
experiments of 5 to 10 samples. No sixth full run.

### Caveats

- The model is not deterministic: the same configuration moves by about 2 samples at repeat 5 (runs 4
  and 5 have identical prompts and differ by 2 samples in total and by different cases). Read one case
  at one run with that in mind.
- The products companion is a measured perturbation fix, not a root-cause fix: the failing outputs were
  answers ("let me check") instead of `product_stock_read`, and the likely reason (not shown) is that the
  record summary already holds the stock count, so the model sometimes answers from it. Any one of the
  other five areas as companion worked as well as inbox did.
- `{email, hr}` plans carry the booking step shape without its prose (ledger ruling: revisit if stray
  booking steps appear on staging).
- The weak-approve suppression has gaps (reviewer probes): "approve Razak's reply about the onboarding
  task" selects `{email, hr}` without inbox, and with the inbox tab open, or through the previous-message
  pass, "approve Aina's laptop task" selects inbox again (the combination that scored 0/5 in the
  forced-area experiment). The tab and record signals cover the first case, not the second. Widening
  covers neither: both selections are valid and in-area, so nothing widens.
- The weak who-is rule is lost when any other word matches: "who is the customer Razak" selects only
  inbox and "who is Danial in HR" only hr (`directory_lookup` is email-only). Widening does not cover
  these in-area misses: when the model follows a too-narrow schema it answers or clarifies in-area, and
  0 of 625 samples widened shows the model follows the schema it is shown. Recall therefore depends on
  the selector and on the scoped-turn log (below).
- Widening also happens on a `resolve` refusal; that is outside the gate and is a ruling.
- Each scoped turn now logs one content-free line, `workspace_scoped_turn` (selected areas, widen
  cause `none`, `out_of_area` or `invalid_output`, and the final action; no text, no ids), so the live
  widened share can be read against the 10% ceiling and an in-area miss can be audited.
- Phase B (the decider's area questions) is not built; the planner gate does not measure it.

### Blockers for the switch-on PR

1. Residual selector recall (final review, I1): communication, stock and calendar phrasings that
   the 125 cases do not contain ("write to Danial about the delay", "let Aina know ...", "set up a
   call ...", "how many blue mugs do we have" on another tab). The gate's 0 misses holds by
   construction on those 125 cases; tight words were added afterwards (no measured case changed its
   selected set), but other phrasings may still be missed, and widening cannot see an in-area miss.
2. Changed after the gate: the company's clock now travels on every scoped turn (final review,
   I2), and the new smoke case `hr_date-en-1` was added after the gate. **Measured 2026-11-20** by
   the confirm run below (623/630, `hr_date-en-1` 5/5).
3. Staging switch-on must watch the `workspace_scoped_turn` log (areas, widen cause, final action)
   to audit in-area misses, not only the widened share.
4. The products plus inbox companion and the weak-word rules (approve, who is) were tuned on
   5-to-10-sample evidence; treat them as measured perturbation fixes, not proven ones.

### Phase B PR notes (branch `feat/area-prompts-decider`, built after phase A)

Points for the phase B pull request text:

- What it does: the routing request can also carry the six area questions; the area answers are
  unioned into the selector's set (Jev adds an area, never removes one). The request asks them
  only when they can be used: `WORKSPACE_AREA_PROMPTS` is on, or the routing decider's mode is
  `shadow` (production keeps its real-traffic area log). With the switch off and any other mode the
  request holds the five fact questions it held before, and the areas are the word rules' alone
  (`should_ask_areas` in `csa/workspace/routing.py`, derived from existing state; no new setting).
  `scripts/assistant_jev_eval.py` always asks all eleven.
- Routing rows in `decision_log` show more `llm` / `low_confidence` fallbacks once eleven answers are
  asked: any one answer below its floor flags the row (the cascade floor is per question, so a weak
  area answer does not change a fact answer; only a timeout, a missing or a malformed answer sends
  the whole request to the word rules).
- A Jev "yes" for inbox joins the set after the weak approve/reject suppression, so a turn the
  suppression kept as `{hr}` can become `{hr, inbox}`; that combination scored 0/5 in the
  forced-area experiment, and the scoped plus cascade path is not measured by the planner gate.
- Before routing goes to `cascade` for areas, the owner runs `scripts/assistant_jev_eval.py` with
  their own key, compares the five fact questions against the last five-question run, and reads
  `false_yes` per area question.
- After merge, watch the `jev_call` log line (`questions=`, `latency_ms`): where the request asks
  the area questions (the routing decider in `shadow`, as on production, or the switch on) it holds
  eleven questions where it held five, which can change its latency. With the switch off and any
  other mode it still holds five.

### Owner decisions still open

- Whether a switch-on PR for staging (`WORKSPACE_AREA_PROMPTS`; the `workspace_scoped_turn` log
  exists) follows now. The switch is off by default and no rollout file changed. The design's one
  final staging smoke run after the switch is on needs its own yes (the sixth full run, the confirm
  run below, used the budget).
- Phase B is built and merged (#172). Routing goes to `cascade` for areas only once
  `scripts/assistant_jev_eval.py` meets its targets for the area questions (see the evaluation
  below).
- Whether to accept the `{email, hr}` booking-step-shape ruling and the `resolve` widening ruling.

### After the merges (2026-11-20)

Phase A (#171, with the test-settings fix #173) and phase B (#172) are merged; staging runs d5e2346e
with `WORKSPACE_AREA_PROMPTS` still off.

- **Confirm smoke run** (owner's yes, throwaway container, `--areas --no-widen`, whole file at 5
  samples = 630, 126 cases, same-day baseline 602/625): 623/630. `smoke_compare` printed no case
  down and seven up; its one VIOLATION line is `hr_date-en-1` missing from the baseline (the case
  was added after the baseline; it scored 5/5). The failing samples are the same cases that failed
  on the baseline: `hr_process_neg-ms-5` 0/5 (open), `email_batch_rest-en-1` 4/5,
  `email_batch_rest-ms-1` 4/5 (was 1/5). This was the sixth and last full run. Its raw output file was in the session's scratch
  directory, which was cleared before it could be committed, so only these figures (read from the
  run's `smoke_compare` and FAIL output) are recorded; the earlier runs' files are in the assets directory.
- **Jev evaluation** (owner's key, 194 labelled messages): 14 of 15 questions met their target, all
  five existing routing and email questions, injection at 100% (target 95%) and five of the six
  area questions. `needs:area:hr` scored 87% on 111 confident answers (target 90%): 10 confident
  yes where the label said no, 4 the other way, Malay 82%. At least three of the named mislabelled
  cases are manager look-ups the selector already sends to hr, so the labels (not Jev) disagreed
  with the design; PR #174 relabels five of them. The per-case ids beyond five are not printed, so
  the re-run is the check. Routing stays out of `cascade` for the hr area question until it meets
  its target; a false yes only widens a prompt and a false no leaves the word rules, so nothing a
  user sees depends on it today.
