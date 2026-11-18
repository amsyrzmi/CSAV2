# HR assistant slice 1c-i: the assistant answers HR process questions in English and Malay (design)

Status: design, 2026-11-18; built the same day (branch `feat/hr-assistant-read`; the "As built" notes in
sections 4 and 6 say where the build differs from the first wording). Builds on `2026-11-16-hr-processes-design.md` section 9 ("slice 1c, own small PR")
and the assistant designs of 2026-10-26 (workspace assistant), 2026-10-31 (Malay) and 2026-11-09 (engine).

## 1. Intent and why

HR PR 1 to 4 gave the company onboarding and offboarding processes, tasks, reminders and an org chart, but the
staff assistant (the dock) knows nothing about them. A manager cannot ask "what is still open for Aina's
onboarding?" and a new starter cannot ask "what are my tasks?". The owner wants the assistant to **recognise and
reply in Malay for HR questions and to recognise the HR features by Malay words**, without renaming or translating
the console UI (decision 2026-11-18: no Malay UI renaming; on-screen names stay as the console shows them inside
Malay sentences, as in the Malay design of 2026-10-31). The HR default templates are English only, so the answers
read English task titles inside Malay sentences.

## 2. Goals, non-goals, anti-goals

Goals
- One new read-only assistant action that answers five HR process questions, in the turn's language:
  1. **my tasks**: "What are my tasks?" / "Apa tugasan saya?"
  2. **a person's process**: "What is outstanding for Aina's onboarding?" / "Apa yang belum selesai untuk onboarding Aina?"
  3. **who is onboarding** (starting soon or in progress)
  4. **who is leaving** (offboarding in progress)
  5. **what is overdue**
- Visibility is exactly the console's: the database decides (HR team sees all; a manager sees direct reports'
  titles, status and dates only; a task owner sees their own tasks; a person sees their own onboarding and
  offboarding). The assistant never filters on a role guess and never widens access.
- Malay and English are equal: recognition (planner prompt and Malay word markers), answers (server text in the turn's
  language, Malay dates), tests (an English and a Malay smoke case per view, plus negative cases).
- No migration, no new proposal kind, no web change.

Non-goals (slice 1c-ii or later)
- The confirm card "mark my task done" (needs a new proposal kind, a migration, four web files and a non-assistant HR
  session; own small PR next). No approve, send back, skip, reschedule, reassign, cancel or close from the assistant,
  ever (approvals need a distinct human reviewer; HR-only operations stay in the console).
- Starting a process by assistant, template questions, translating console labels, Malay template text.

Anti-goals
- A task note, description or any personal field in the planner prompt, the planner facts or the stored turn row.
- A routing question for Jev: a process read needs no planner fact (the database decides visibility), and a new
  routing group would force a change to all 96 routing labels and the pinned routing tests for no gain.
- The assistant refusing a manager or a new starter with "your access does not include People": process reads do not
  depend on `people_access`.

## 3. Approaches considered

A. **One action `hr_process_read` with an enum `view` (my_tasks, person_process, who_onboarding, who_leaving,
   overdue) and an optional `person`.** One small model in the planner schema; one resolver. Chosen: the planner
   prompt is already about 13 thousand characters and each action adds a schema and a bullet, so one small action
   limits prompt growth and confusion with neighbours.
B. Five separate actions. More prompt and schema, more confusion with `people_query`, `hr_person_open` and
   `hr_person_leave`. Rejected.
C. A Jev routing group `processes`. Churns every routing label and test for a read that needs no fact. Rejected.

## 4. Behaviour

The planner picks `hr_process_read` from the message. Server side, after the planner decided, a new resolver module
(`csa/workspace/hr_processes.py`, patterned on `hr_people.py`) reads through `HrProcesses` (`my_tasks`, `processes`,
`person`, `process`) with the signed-in staff member's own read session (`mutation=False`, not an assistant
session), exactly as the console does, so no new privilege exists. The HR package must be active
(`unlocked(facts.packages, TAB_GATES["hr"])`), otherwise the usual "HR is not on" answer. The answer is markdown in
the turn's language (`AnswerResult`), mapped to `"answer"` in `_STORED_ACTIONS` so the turn table's action CHECK is
untouched.

- **my_tasks**: open tasks assigned to the caller, soonest first, up to 10, each with title, the process and person
  it belongs to, and a due phrase in words ("due tomorrow", "overdue by 3 days"; Malay equivalents). "Nothing waiting
  for you." when none. HR team members also have pool tasks; they are listed as "HR team" tasks. As built, the
  list is the console's My tasks as the database returns it, so it also holds the steps waiting for the
  caller's approval ("waiting for your approval"); who_onboarding and who_leaving list active processes only;
  the overdue view opens at most 50 overdue processes (past that the count reads "N+") and no read scans more
  than 1000 rows.
- **person_process**: the person is resolved from the visible process rows (`employee_name`), not from `HrPeople`
  (a manager or a person is refused the People list but may see their own processes). One match: progress ("5 of 9
  steps done") and the open steps with owner and due phrase (a manager sees titles, owner role, status and dates
  only, because the database returns nothing more). Several matches: the usual clarification (up to 5 options,
  "Name — onboarding, started 3 November 2026"; as built the label carries the kind and the process start date,
  not the job title: process rows have no job title, and reading it from People would refuse a manager or a
  new starter; the option value is the process id and comes back in `person`). No match: "I can't find a process for that person that you can see." (never revealing whether
  the person exists).
- **who_onboarding / who_leaving**: active onboarding or offboarding processes the caller can see, soonest next
  due first, up to 10, with progress and "overdue" marks; a count line when more.
- **overdue**: overdue tasks the caller can see (their own, and for HR/managers per visibility), up to 10.
- Language: the turn's language decides the answer (existing rule); Malay uses "anda", the glossary of the earlier
  Malay design (for example "hari terakhir", "akan berhenti", "Pengurus") and `format_day` Malay months; console names
  (My tasks, Processes, People, Mark done) stay English inside Malay sentences; a task title is `title_ms` when the
  template has one, otherwise `title_en` (the defaults are English only), and the answer does not hide that.
- Recognition in Malay: the planner prompt names the action with Malay examples ("tugasan saya", "belum selesai",
  "tertunggak", "lewat", "orientasi", "siapa yang sedang onboarding", "siapa yang akan berhenti") and
  `_MALAY_MARKERS` gains a few process words (tugas, tugasan, selesai, tertunggak, lewat, orientasi, proses) so a
  message made only of those words still detects Malay; both are pinned by tests.
- Disambiguation (prompt rules plus negative smoke cases): "Mark Aina as leaving" stays `hr_person_leave`; "open
  Siti's profile" stays `hr_person_open`; "who reports to Razak" and "who is in Finance" stay `people_query`; "mohon
  kursus" stays `training_submit`; a request to complete, approve or change a task is answered with a plain sentence
  that it is done in the console (My tasks), never a card.

Stored turn: the stored result keeps counts and fixed text only (the existing `_Planned.stored` method); names and
titles are shown to the caller but not written to the tenant-readable turn row beyond what `people_query` already
stores.

## 5. Privacy and safety

- Visibility only from the database functions; a test per role (HR team, manager, person, task owner, a member with
  no linked employee) proves the assistant returns no more than the console API for the same session.
- No process content in planner facts or the system prompt (`test_planner_facts_never_hold_personal_fields` stays
  green and gains a process case).
- Read only: no write executor, no proposal.

## 6. Testing and evidence

- Python: contract and planner tests (pinned prompt strings, language detection with the new markers), resolver
  tests per view and role in English and Malay using the process fixtures, privacy tests, the contract test that
  `ProposalKind == registry executors` stays unchanged.
- Smoke: for each of the five views an English and a Malay case, plus negative cases (leaving, profile, people query,
  training, "mark my task done", a non-HR staff member), added to `csa/workspace/smoke_cases.json`,
  `FEATURES` in `tests/test_assistant_smoke.py`, `docs/ASSISTANT_RETEST.md`, and the Jev label set decision (the new
  cases go in `SMOKE_LEFT_OUT` with the reason "no routing question").
- **A planner prompt change forces a full `assistant_smoke` run before merge** (as built, 95 cases: about 106
  model calls, up to about 201 with re-plans, with the owner's model key and the owner's yes). The controller asks
  first and reports the failures as printed. As built there are 20 new cases (ten positive, ten negative), and the
  run has not been made yet.

## 7. Risks

Prompt growth and planner confusion with neighbouring actions (mitigated by one action, explicit disambiguation and
negative cases); Malay detection of short process-only messages (markers pinned); an HR-package-off company (usual
answer); a person with several processes (clarification); long lists (capped at 10 with a count line); another
session editing the planner, contracts, service, rules or smoke files (the Overview assistant work is already
merged; re-check right before the PR).

## 8. Left to the builder

Exact English and Malay sentences within the glossary above; the module layout of `hr_processes.py`; the `person`
matching rule over visible rows (reuse `people_rules.search` words if it fits); whether `overdue` merges with
`my_tasks` internally.

## 9. Next (not in this PR)

Slice 1c-ii: "mark my task done" as a confirm card (new `ProposalKind`, migration `0637` by the live-list method,
web edits, a non-assistant HR session, version-bound card, never an approval or an auto-check step), after this PR
has run on staging and the owner has retested the Malay wording.
