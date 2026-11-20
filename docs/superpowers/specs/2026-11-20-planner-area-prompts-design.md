# Planner area prompts: each turn sees only the areas it needs

Status: design for the owner's approval. Nothing is built.

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

- No second model call and no model-based router: the area choice is deterministic server code.
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

## The selector (deterministic, no model)

`select_areas(message, page_context, previous_action, facts) -> frozenset[Area] | None`, a pure
function. Signals, unioned (it errs on including more, like `Needs` in `csa/workspace/routing.py`):

1. The open console tab (`page_context.tab`): `hr` to hr, `email` to email, `inbox` to inbox and email,
   `bookings` to bookings, `training_library` to training, `business` to products; other tabs add
   nothing.
2. Word rules in English and Malay per area, built from the same trigger words the action bullets
   already use, and extending `word_rule_needs` (mail, team, training, people, manager) in
   `csa/workspace/service.py`.
3. The previous turn's action (`history[-1].action`), so follow-ups ("the rest", a picked person, an
   answer to a clarification) stay in the area of the turn they continue.
4. A colleague pick ("Use employee ... for: ...") keeps email.

If no signal fires, the selector returns None and the full prompt (today's) is used. A scoped prompt
is general plus the union of the selected areas. A selected set of two or more areas is allowed and
normal (the cross-area case "email Aina about her leave" selects email and hr).

## Safety net: out-of-area output

`PlannerOutput` still validates the full union, so a model that names an action outside the selected
areas returns a valid contract. The server checks `proposal.action` against the selected areas'
actions; if it is outside, the turn's one allowed re-plan (`_corrected_plan`, at most two model calls
per turn today) runs with the full prompt and is final. This costs a second call only on that failure.
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
2. **Selector assisted by the routing decider (Jev).** It could add areas, never remove one, like the
   fact reads. It adds a model call (about 0.4 s and a failure mode) and is in shadow on production
   today. Deferred: it can be added to the selector later without changing anything else.
3. **Restructure HR only first.** The owner chose to restructure every area in this round.

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
