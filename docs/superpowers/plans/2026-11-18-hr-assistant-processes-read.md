# HR assistant slice 1c-i: read-only process answers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The staff assistant answers five HR process questions (my tasks, a person's onboarding or offboarding, who is onboarding, who is leaving, what is overdue) in English and Malay, read only, with visibility taken from the database.

**Architecture:** One new planner action `hr_process_read` with an enum `view`; a resolver module `csa/workspace/hr_processes.py` patterned on `hr_people.py` that reads through `HrProcesses` with the caller's own non-assistant read session and renders an `AnswerResult` in the turn's language; stored as `"answer"` through `_STORED_ACTIONS`, so there is no migration, no proposal kind and no web change.

**Tech Stack:** Python/FastAPI/pydantic (csa/workspace), the existing HR process service (csa/hr/processes.py), pytest, the assistant smoke runner and its JSON cases.

**Spec:** `docs/superpowers/specs/2026-11-18-hr-assistant-processes-design.md` (and `2026-11-16-hr-processes-design.md` section 9, `2026-10-31-assistant-malay-design.md`).

## Global Constraints

- Read only: no executor, no `ProposalKind`, no migration (`EXPECTED_SCHEMA_VERSION` stays `"0636"`), no web change; `test_workspace_contracts.py` assertion `set(ProposalKind) == set(registry._executors)` stays untouched and green.
- Visibility only from the database functions behind `HrProcesses.my_tasks / processes / person / process`, called with `hr_session_from(request, actor, client_id, mutation=False)` (a non-assistant session); never filter on a role guess in Python; never widen access.
- The HR package must be active (`unlocked(facts.packages, TAB_GATES["hr"])`); a process read does NOT depend on `people_access`, and the planner prompt must say so.
- No process content (task note, description, name, title) in planner facts, the system prompt, or the tenant-readable stored turn beyond counts and fixed text (`_Planned.stored`); `test_planner_facts_never_hold_personal_fields` stays green.
- Language: the turn's language decides; Malay uses "anda", the glossary of the Malay design and `hr_people.format_day` (never the locale); console names (My tasks, Processes, People, Mark done) stay English inside Malay sentences; a task title is `title_ms` if present, else `title_en`.
- The assistant never completes, approves, sends back, skips, reschedules, reassigns, cancels or closes anything: a request to do so gets a plain sentence that it is done in the console (My tasks).
- Prompt growth is limited to one small action model and its bullets; every prompt change keeps the pinned prompt tests green and requires a full `assistant_smoke` run before merge (owner's model key and the owner's yes, never CI).
- Repo is PUBLIC: synthetic names only (Aina, Danial, Razak, Siti, Mei Ling are the existing test names).
- Caps: lists of at most 10 rows with a count line when more.

## Review Focus

1. A manager must see only titles, status and dates of a direct report's process (no notes, descriptions, events); a person only their own onboarding and offboarding; a member with no linked employee and no HR authority nothing: the answer must equal what the console API returns for the same session, never more.
2. A person with the same name in two processes, a person with no visible process, a name that matches nobody visible: clarification up to 5 options, and "I can't find a process for that person that you can see" without revealing whether the person exists.
3. A message made only of Malay process words (for example "tugasan tertunggak") must detect Malay and answer in Malay; an English message in a Malay thread follows the existing carry rules.
4. "Mark Aina as leaving", "open Siti's profile", "who reports to Razak", "mohon kursus" and "mark my task done" must NOT become `hr_process_read` (negative smoke cases); "mark my task done" gets the console sentence, not a card.
5. HR package off, HR switch off, and a read-only member: the usual refusal sentences, no crash, no leak; long lists are capped at 10 with a count line; a task with no Malay title falls back to English and says nothing odd.

---

### Task 1: The action, the prompt and the Malay markers

**Files:**
- Modify: `csa/workspace/contracts.py` (new `HrProcessReadProposal` near `hr_person_open` ~293-343; add to `WorkspaceProposal` ~464), `csa/workspace/planner.py` (`PlannerProposal` ~55, `SYSTEM_PROMPT` ~280 actions bullets and rules, `_MALAY_MARKERS` ~237), `csa/workspace/service.py` (`_STORED_ACTIONS` ~150-154)
- Test: `tests/test_workspace_planner.py` (pinned prompt strings, detection ~211-236), `tests/test_workspace_contracts.py`

**Interfaces:**
- Produces `HrProcessReadProposal(action: Literal["hr_process_read"], view: Literal["my_tasks","person_process","who_onboarding","who_leaving","overdue"], person: str | None = None)` (person only for `person_process`; a validator rejects a missing person for that view and a person for the others); `_STORED_ACTIONS["hr_process_read"] = "answer"`.
- Prompt: one Actions bullet naming the five views with English and Malay example phrases, the rule that process reads do not depend on `people_access`, the disambiguation sentences from the spec section 4, and the sentence that completing, approving or changing a task is done in the console (answer, no card). As built, the bullet also says `person` may be "the option value the user picked from a list you offered" (a process id), and the people-actions rule names `hr_process_read` too; both are pinned by tests.
- `_MALAY_MARKERS` gains: `tugas`, `tugasan`, `selesai`, `tertunggak`, `lewat`, `orientasi`, `proses` (check none is shared with English markers).

- [ ] **Step 1: Write failing tests**: the model validates (view and person rules); `PlannerProposal` accepts it; the pinned prompt contains the new bullet and the three rules (use the repo's pinned-prompt test style); `detect_language("Apa tugasan tertunggak saya?") == "ms"` and `detect_language("tugasan tertunggak") == "ms"`; an English process sentence stays "en"; `_STORED_ACTIONS` maps it to `"answer"`; `test_workspace_contracts` kind/executor equality unchanged.
- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement** the model, union entries, prompt text, markers and the stored mapping.
- [ ] **Step 4: Run planner and contract tests plus ruff; commit** `feat(assistant): the hr_process_read action, its prompt rules and Malay process words`.

### Task 2: The resolver, the answers and the wiring

**Files:**
- Create: `csa/workspace/hr_processes.py`
- Modify: `csa/workspace/service.py` (`_resolve` branch ~1881; `_TEXT` en ~559-696 and ms ~701-800), `csa/workspace/rules.py` only if a correction note is needed
- Test: `tests/test_workspace_hr_processes.py` (new), `tests/test_workspace_hr_people.py` (planner-facts privacy case extended)

**Interfaces:**
- Consumes `HrProcesses(hr_service)` methods `my_tasks(session, status=None)`, `processes(session, kind=None, status=None, overdue=None, ...)`, `person(session, employee_id)`, `process(session, process_id)` and models `MyTask`, `ProcessRow`, `ProcessDetail`, `PersonProcesses` from `csa/hr/process_models.py`; `hr_session_from(request, actor, client_id, mutation=False)` from `csa/hr/api.py:136`; `unlocked`, `TAB_GATES`; `hr_people.format_day`.
- Produces `async def resolve_process_read(request, actor, client_id, proposal, facts, text) -> ProcessAnswer` (as built: `ProcessAnswer` holds `shown` and an optional `stored` copy, and `service._process_read` maps it to `_Planned`, because `_Planned` is private to `service.py`), returning an `AnswerResult` markdown in `facts.language`, or a clarification for several matching processes. As built, the clarification options read "Name — onboarding, started 3 November 2026" (kind and process start date, valued by process id; the plan's "Name, Job title" was dropped because process rows have no job title), and a task title is `title_ms` in Malay only when the template has one.

- [ ] **Step 1: Write failing tests** using `tests/hr_process_world.py` or the repo's process DB fixtures (read how `tests/test_hr_process_*.py` build processes): per view and per role (HR team, a manager of a direct report, the person, a task owner, a member with no linked employee, HR package off) in English and Malay: my_tasks with and without tasks; person_process one match / several / none / name that matches a person the caller cannot see; who_onboarding, who_leaving, overdue with 0, 3 and 12 rows (cap 10 plus a count line); a manager's answer contains no note or description text even when the task has one; Malay answer text uses "anda", Malay months, English console names and the `title_en` fallback; the stored result holds no names or titles (assert on the stored copy); the privacy test that planner facts hold no process content; a plain non-HR staff member gets the console sentence, not an error.
- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement `hr_processes.py`** (view dispatch, visible-row person matching reusing `people_rules.search` words if it fits, due phrases in words, caps, stored copy) and the `_resolve` branch and the `_TEXT` en and ms keys (Malay copy matches the glossary of `service._TEXT["ms"]` and `executors._CARD_TEXT["ms"]`).
- [ ] **Step 4: Run the new and neighbouring workspace tests (`test_workspace_hr_people.py`, `test_workspace_people.py`, `test_workspace_planner.py`, `test_workspace_contracts.py`, `test_workspace_routing.py`) plus ruff, pyright on changed files; commit** `feat(assistant): answer HR process questions from the caller's own visibility, in English and Malay`.

### Task 3: Smoke cases, labels and the retest rows

**Files:**
- Modify: `csa/workspace/smoke_cases.json`, `scripts/assistant_smoke.py` (`ActionName` Literal ~104, a process preset in `PRESETS` if needed), `tests/test_assistant_smoke.py` (`FEATURES` ~30), `tests/test_assistant_jev_eval.py` (`SMOKE_LEFT_OUT` ~149 with the reason "no routing question"), `docs/ASSISTANT_RETEST.md`
- Test: `tests/test_assistant_smoke.py`, `tests/test_assistant_jev_eval.py`

**Interfaces:**
- Produces (as built: 20 new cases, 95 in the file, about 106 model calls per run and up to about 201 with re-plans, because one drafting call runs for each of the 11 draft cases; the cost line is in `docs/ASSISTANT_RETEST.md` and the `scripts/assistant_smoke.py` docstring; the non-HR question about a colleague's onboarding is `hr_process-en-2` / `ms-2`, which must plan `hr_process_read`) smoke cases `hr_process-en-N` / `hr_process-ms-N` for each of the five views (ten cases), and negative cases `hr_process_neg-en-N` / `-ms-N`: "Mark Aina as leaving" -> `hr_person_leave` (not `hr_process_read`), "Open Siti's profile" -> `hr_person_open`, "Who reports to Razak?" -> `people_query`, "Mohon kursus latihan pertolongan cemas" -> `training_submit`, "Mark my task done" (and Malay) -> `answer` with `not_actions: ["hr_process_read"]` and no card, and a non-HR staff question about a colleague's onboarding -> `hr_process_read` or `answer` (state which and why in the case note). Every case's message must satisfy `detect_language(message) == case.language`; every id appears as a backticked id in `docs/ASSISTANT_RETEST.md` (a test checks both directions).

- [ ] **Step 1: Add the cases and the retest rows; run `tests/test_assistant_smoke.py` and `tests/test_assistant_jev_eval.py`, expect FAIL until the registrations are made.**
- [ ] **Step 2: Register `FEATURES`, `ActionName`, presets and `SMOKE_LEFT_OUT`; run both test files green; commit** `test(assistant): smoke cases and retest rows for HR process questions in English and Malay`.
- [ ] **Step 3 (controller, not the implementer):** ask the owner for the yes to run `docker exec csa-staging-gateway-1 python -m scripts.assistant_smoke` on staging after the rollout, and report the failures as printed.

### Task 4: Docs

**Files:**
- Modify: `docs/ASSISTANT_RETEST.md` (the owner's manual rows: ask in English and Malay, as HR, as a manager, as a new starter), `docs/hr/SETUP.md` (a short "Ask the assistant" paragraph), `docs/PROGRESS.md`, `docs/hr/PROGRESS.md`, the HR processes spec section 9 (a dated note: slice 1c-i built, 1c-ii next)
- Test: the docs guard tests the repo has, ruff, `make lint` if cheap

- [ ] **Step 1: Write the docs exactly as built** (the five views, the visibility statement, "the assistant does not complete tasks; do that in My tasks", the Malay behaviour and the English-only task titles, the smoke run and its status, no migration, images only rollout, known limits: caps, no write, no approvals).
- [ ] **Step 2: Run the guard tests and lint; commit** `docs(assistant): HR process questions, retest rows and progress`.

## Not in this PR

Slice 1c-ii (the "mark my task done" card, migration `0637`, web edits); translating console labels; Malay template text; starting or cancelling processes by assistant; any routing question for Jev.
