# HR processes, console (PR 3): My tasks, Processes tab, template editor, person section, Home reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Staff can see and act on their HR tasks, HR can define templates and watch processes, and the person's profile and Home show onboarding and offboarding progress and reminders, all on top of the merged backend (migrations 0630-0635).

**Architecture:** A small backend slice first (migration 0636: the template preview read and the explicit "replace" flag on manual start), then the web console: a typed API client and copy for the new routes, a My tasks panel (Home and My HR), a Processes tab (list, detail, start, cancel, close, templates), the process section on the person profile with the rehire warning, and Home reminders that read `subject` and `due_on`. Console only: no new switch, no assistant cards.

**Tech Stack:** Next.js/React (TypeScript), node unit tests, Playwright browser tests with the repo's stub world, FastAPI/Postgres for the 0636 slice.

**Spec:** `docs/superpowers/specs/2026-11-16-hr-processes-design.md` (sections 5.3, 5.5, 5.6, 6, 8, 12) and the PR 2 hand-over list in `docs/PROGRESS.md` ("HR processes, backend part 2"). The merged API is documented in `docs/CONSOLE_API.md` (HR processes sections).

## Plan decisions (mine; the owner may overrule)

- **Who sees the Processes tab:** the HR owner and holders of Manage people (the HR team), the same rule the backend uses. Managers and task owners get **My tasks** (Home for HR duties holders, `MyHr` for everyone else) and their reports' progress through the person profile; a managers' processes list is a later slice. `visibleTabs` already has the HR-team flags; no backend context change.
- **Templates live inside the Processes tab** (a "Templates" sub-view), not under Settings: Settings is owner-only, templates are open to HR owner and Manage people holders (backend rule).
- **Replace on manual start is explicit:** `POST /processes` gets `replace: boolean` (default false): when a process of the same kind is active and `replace` is not true the API refuses with `process_already_active` (409); with `replace: true` the old one is cancelled as today. The console asks first.
- **The template editor's preview** ("applies to N people now" and the conflict list) is a new definer read `hr_template_preview(p_client, p_kind, p_rules jsonb)`; HR team only; returns the count of active people each rule set matches and the ids of other active templates it ties with. Route `POST /console/v1/hr/templates/preview` (a POST because the body is the draft rules; it writes nothing, needs no idempotency key but needs CSRF).
- **Reordering steps:** up/down buttons (no drag-and-drop library exists, none is added).
- **Language:** the console UI stays English (it has no locale switch); step titles and descriptions carry their Malay text (`title_ms`, `description_ms`) which the editor edits side by side and My tasks shows in English with the Malay line below when present. Reminder lines come from `subject` (English).
- **Migration lane:** 0636 only (HR lane 0630-0649). `EXPECTED_SCHEMA_VERSION` becomes "0636".

## Global Constraints

- Repo is PUBLIC: synthetic data only; never read `/srv/csa-staging/secrets`.
- Web: no colour literals in `components/console/hr/` (use `var(--fs-*)` tokens and `ink-*`/`brand-*` classes: `tests/theme-literals.test.mjs` enforces); every new `lib/*.ts` and test must be registered in `scripts/check-isolated.mjs` (files and commands) or the staged browser build breaks; error codes get plain sentences in a copy table (WRITING_GUIDE "Errors": say what the person can do, name the thing not the code, never blame; Malaysian English; dates like "27 Oct 2026"); phone layout: cards below `md`, tables from `md`, no horizontal scroll at 320 px in the three themes (browser tests loop `WIDTHS=(320,390,768,1024,1440)` and `THEMES`); the new routes must be served by the browser stub `RecordsWorld.answer`, and unexpected calls fail the test.
- Every write sends CSRF and a stable `Idempotency-Key` (reuse the `write()` pattern of `lib/hr-api.ts`); errors surface as `ApiError.code` with a sentence from the copy table, never raw codes.
- The browser never decides authority: it hides controls the API would refuse (read-only members, non-HR), and still handles the refusal (404 `resource_unavailable`) gracefully.
- Backend (Task 1 only): same rules as the earlier plan (definer functions, pinned search_path, EXECUTE to `csa_app` only, no table privileges), `ruff`, `pyright` on the whole project including tests, tests one file at a time, regenerate `docs/console_openapi.json`.
- Docs: `docs/CONSOLE_API.md`, `docs/PROGRESS.md` entry, a short `docs/hr/PROGRESS.md` line; a new operator note for the HR owner only if a screen needs one.

## Review Focus

- A read-only member or a non-HR staff member must see no write control and no HR-team data (templates, others' processes, notes, events).
- A manager's My tasks approval rows have withheld fields as `null`: show nothing there, not "No description".
- Phone width at 320 px in all three themes for every new screen; keyboard and focus order; dialogs trap and restore focus.
- A step reorder, a stale `expected_version` (409 `template_changed`/`task_changed`) and a double click must never lose or duplicate an edit.
- The rehire dialog warns when an offboarding is open; manual start asks before replacing; cancel and close-anyway confirm first.
- Home reminders: many rows are grouped; a reminder for a task that is no longer open does not pretend to be actionable.

---

### Task 1: Backend slice, migration 0636 (preview read and explicit replace)

**Files:**
- Create: `db/migrations/0636_hr_template_preview.sql`, `tests/test_hr_template_preview.py`
- Modify: `csa/hr/process_models.py` (`TemplatePreview*`, `ProcessStart.replace`), `csa/hr/processes.py`, `csa/hr/process_routes.py`, `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION` "0636"), `db/migrations/README.md`, `db/sql/hr/readiness.sql` (new function name), `tests/test_hr_persistence.py` (allowed-path list, exact set), `tests/test_hr_process_api.py`, `tests/test_hr_process_models.py` (guard), `docs/CONSOLE_API.md`, `docs/console_openapi.json`, `docs/PROGRESS.md`
- Test: `tests/test_hr_template_preview.py`, `tests/test_hr_process_api.py`, `tests/test_hr_process_instances.py`, `tests/test_hr_persistence.py`, `tests/test_console_contract_snapshot.py`

**Interfaces:**
- Produces: `hr_template_preview(p_client uuid, p_kind text, p_rules jsonb) RETURNS jsonb` = `{matching: int, ties: [{template_id, name}], total_active: int}` (HR team only, tenant checked like `hr_template_get`, same rule matching as `hr_process_match` without choosing a winner: counts active people whose department, employment type, legal entity and region match every set rule; `ties` = other active templates of the kind with the same number of set rules that overlap); `POST /console/v1/hr/templates/preview` `{kind, rules}` -> `TemplatePreview`; `hr_process_start_apply` (in 0636, `CREATE OR REPLACE` copying 0634's body with one change): body field `replace` (default false); an active process of the same employee and kind with `replace` not true raises `hr_process_already_active` (mapped to 409 `process_already_active` already) instead of superseding; the automatic trigger path is unchanged (rehire cancel and supersede stay).

- [ ] **Step 1: Write the failing tests:** `test_preview_counts_people_each_rule_matches` (department, employment type, legal entity trimmed and case-insensitive, region; null rule means any); `test_preview_lists_ties`; `test_preview_is_hr_team_only_and_tenant_isolated`; `test_manual_start_refuses_when_one_is_active_unless_replace`; `test_manual_start_replace_cancels_the_old_process_with_an_event`; `test_trigger_paths_still_supersede`; API: `POST /templates/preview` CSRF and 404 for non-HR; the guard tests (`test_hr_process_models`) pass once the new refusal text and model fields exist.
- [ ] **Step 2: Run** each file: FAIL. **Step 3: Implement** the migration and Python. **Step 4: Run** the files above plus `tests/test_migrate.py`, `tests/test_health.py`, then `ruff`, `pyright`, `lint-imports` on changed files: PASS.
- [ ] **Step 5: Commit** `feat(hr): template preview and explicit replace on manual start`.

### Task 2: Web API client, copy and helpers

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/hr-process-api.ts`, `web/lib/hr-process-copy.ts`, `web/lib/hr-process-view.ts`, `web/tests/hr-process-api.test.mjs`, `web/tests/hr-process-view.test.mjs`
- Modify: `web/lib/hr-paths.ts` (`HrTab` + `HR_TABS` gain `processes`; `HrRoute` gains `processes` list, `processes/<id>` and `processes/templates`, `processes/templates/<id>`), `web/lib/hr-api.ts` (export what the client needs: `write()` pattern or a shared helper), `web/components/console/hr-guide/TabIntro.tsx`, `web/scripts/check-isolated.mjs` (register new lib files, tests, commands), `web/tests/hr-paths.test.mjs`, `web/tests/hr-nav.test.mjs`
- Test: the `.mjs` files above plus `npm run lint` and typecheck

**Interfaces:**
- Produces `processClient(tenant)` with: `templates()`, `template(id)`, `createTemplate(body)`, `updateTemplate(id, body)`, `deactivateTemplate(id, expected_version)`, `previewTemplate(kind, rules)`, `processes(filters, after?)`, `process(id)`, `startProcess(body)`, `cancelProcess(id)`, `closeProcess(id, note)`, `personProcesses(employeeId)`, `myTasks(status?, after?)`, `taskAction(id, action, body)` for `complete|approve|send-back|skip|reschedule|reassign|note`; every type mirrors the OpenAPI schema names (`TemplateOut`, `ProcessRow`, `ProcessDetail`, `MyTask`, ...) with optional fields where the API omits them; a `processErrorText(code)` copy table covering every refusal code the SQL and API return (`task_changed`, `task_closed`, `task_own_process`, `check_not_met`, `template_changed`, `process_no_template`, `process_already_active`, ... read `csa/hr/processes.py::PROBLEMS` and `docs/CONSOLE_API.md` table: same wording, plain sentences); view helpers: `dueText(task, today)` ("Due in 2 days", "Overdue by 3 days", "No date yet"), `statusLabel`, `ownerRoleLabel`, `offsetSentence(anchor, offset)` ("5 days after the start date"), `progressText`, `groupReminders(kinds)`.

- [ ] **Step 1: Write the failing node tests:** one case per helper including the boundaries (today, tomorrow, overdue 1 day, null due), `offsetSentence` for negative/zero/positive offsets and each anchor, every API refusal code has a non-empty sentence that contains no code string, `parseHrPath` for the new routes and the exact `HR_TABS` list including `processes`, `visibleTabs` shows Processes only for owner/Manage people, client builds the exact URLs and bodies (assert method, path, query and the `replace` field).
- [ ] **Step 2: Run** `npm test` for those files: FAIL. **Step 3: Implement. Step 4: Run** the node tests, `npm run lint`, typecheck: PASS.
- [ ] **Step 5: Commit** `feat(console): HR process client, copy and helpers`.

### Task 3: My tasks (Home and My HR)

**Files:**
- Create: `web/components/console/hr/MyTasks.tsx`, `web/components/console/hr/TaskActions.tsx`, `tests/test_hr_my_tasks_browser.py`
- Modify: `web/components/console/hr/HomeTab.tsx`, `web/components/console/hr/MyHr.tsx`, `tests/test_hr_access_browser.py` (`RecordsWorld.answer` serves `/hr/tasks/mine` and the task writes) or a new stub module imported by the new test
- Test: `tests/test_hr_my_tasks_browser.py`

**Interfaces:**
- Consumes: Task 2 client. Produces `<MyTasks tenant readonly />` shown on Home (above reminders) and in `MyHr`; each row: title (English, Malay line below when present), process and person name, due text, status chip, "Done by HR" note when `done_by_hr`, the send-back reason when present, the shared note; actions for the caller's own tasks: Mark done (with optional note), Add note; approval rows (`needs_my_approval`): Approve and Send back (reason required in a dialog, 1-500 characters); rows with `mine=false` (HR's on-behalf rows) show the assignee name and "Mark done for them"; withheld fields (null on approval rows for non-HR) render nothing. `readonly` hides every action. Automatic-check steps show the check hint and a "Check now" action (calls complete; a `check_not_met` refusal shows the sentence). Empty state: "Nothing waiting for you."

- [ ] **Step 1: Write the failing browser tests** with the stub world: assignee sees own row and completes it (note sent, row leaves or shows done); manager sees an approval row without descriptions and approves, then sends back with a reason; HR sees an on-behalf row with the name and completes for them; read-only sees rows without buttons; stale `task_changed` shows the sentence and reloads; double click sends one request; send-back reason shows to the assignee; phone widths and three themes have no horizontal scroll; keyboard: dialog focus trap and restore.
- [ ] **Step 2: Run** the file: FAIL. **Step 3: Implement. Step 4: Run** the file, node tests, `npm run lint`, typecheck: PASS (browser files need more than 3000 MB free).
- [ ] **Step 5: Commit** `feat(console): My tasks`.

### Task 4: Processes tab: list, detail, start, cancel, close

**Files:**
- Create: `web/components/console/hr/ProcessesTab.tsx`, `ProcessList.tsx`, `ProcessDetail.tsx`, `StartProcessForm.tsx`, `tests/test_hr_processes_browser.py`
- Modify: `web/components/console/hr/HrShell.tsx` (`TabContent` case `processes`), `web/lib/hr-paths.ts` if routing needs more, the stub world
- Test: `tests/test_hr_processes_browser.py`

**Interfaces:**
- Produces: the Processes tab for the HR team: filters (kind, status, overdue only, department, owner) with the keyset "Load more" pattern; table from `md`, cards below; row: person, kind, template, progress ("4 of 7 required steps"), next due, overdue chip. Detail (`processes/<id>`): header, timeline of steps (owner, assignee name or "HR team", due, status, who did it, the audit events in a collapsed "History"), actions: Cancel process (confirm), Close anyway (note required, confirm), per-step HR actions reschedule / reassign / skip (reason) via small dialogs reusing `TaskActions`. Start form: person picker, kind (`onboarding`, `offboarding`, `custom`), template picker (required for custom), and when the API answers 409 `process_already_active` an inline confirm "There is already an active onboarding for <name>. Replace it?" which resends with `replace: true`.

- [ ] **Step 1: Write the failing browser tests:** HR sees list and filters; non-HR gets no tab and a direct URL shows the no-access notice; detail timeline and history; start with replace confirm (first request has no replace, second has `replace: true`); cancel and close need confirmation and send the right bodies; skip needs a reason; reschedule rejects a bad date client-side and handles 409; phone widths and themes; keyboard.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** the file, node tests, lint, typecheck: PASS.
- [ ] **Step 5: Commit** `feat(console): Processes tab`.

### Task 5: Template editor (inside the Processes tab)

**Files:**
- Create: `web/components/console/hr/TemplateList.tsx`, `TemplateEditor.tsx`, `StepsEditor.tsx`, `tests/test_hr_templates_browser.py`
- Modify: the Processes tab shell (sub-navigation Processes | Templates), the stub world
- Test: `tests/test_hr_templates_browser.py`, `web/tests/hr-process-view.test.mjs`

**Interfaces:**
- Produces: Templates list (name, kind, who it applies to in words, active, conflicts badge); the editor: name, kind (fixed after create), "Applies to" rules (department picker from `GET /departments`, employment type select from the four values, legal entity text with a datalist of the existing values from the people read, region select), a live preview line ("This applies to 6 people now", the ties as warnings) from `POST /templates/preview` debounced; steps repeater (custom component, 1-40 rows): title (English required, Malay optional), description (multi-line, up to 2000, English and Malay side by side), reference note (500), owner role (HR team, the person's manager, the person, a named employee picker), due offset sentence with number input and anchor select (start date, last day, probation end), required checkbox, approval by (none, HR team, manager), automatic check select, up/down/remove/add buttons; saving sends the full template with `expected_version` and `rules` always present; warnings returned by the API (late leaver step, ties) show above the form after save; 409 `template_changed` reloads with a notice; deactivate with confirm; Copy as new.

- [ ] **Step 1: Write the failing tests:** node: `offsetSentence`, step validators (limits, anchor allowed per kind: onboarding start_date/probation_end, offboarding last_day); browser: create an onboarding template with three steps and see the request body exactly; reorder with the buttons changes `ordinal` order; remove and add keep unique order; description newline round-trips; preview updates after a rule change and shows ties; stale version shows the sentence; a 41st step is refused client-side; read-only and non-HR cannot reach the editor; phone widths and themes; keyboard operation of the up/down buttons with focus kept on the moved row.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** the files, lint, typecheck: PASS.
- [ ] **Step 5: Commit** `feat(console): process template editor`.

### Task 6: Person profile section, rehire warning, Home reminders

**Files:**
- Create: `web/components/console/hr/PersonProcesses.tsx`, `tests/test_hr_person_processes_browser.py`
- Modify: `web/components/console/hr/PersonProfile.tsx` (a "Onboarding / Offboarding" section where the profile shows sections), `web/lib/hr-people-api.ts` (`ProfileSection` gains `processes` if sections are typed there), `web/components/console/hr/LeavingForm.tsx` (`RehireForm` warning), `web/components/console/hr/HomeTab.tsx` and `web/lib/hr-people.ts` (`homeItems`/notification text), the stub world
- Test: `tests/test_hr_person_processes_browser.py`

**Interfaces:**
- Produces: the section for HR, the manager (their reports) and the person themselves: per process the progress, next task, anchor dates, "No onboarding template matches this person" (HR only, link to Templates) and "Set probation end date" (HR, `person.probation_set` with the people version; shows the date, clears it); the person sees only the person view the API returns (progress counts and their own tasks); `RehireForm`: when `GET /people/{id}/processes` shows an active offboarding, the confirm text says "Rehiring cancels the open offboarding and skips its remaining steps (for example final pay checks). Continue?" with the existing inline confirm pattern; Home reminders: group by kind with a count and show `subject` and `due_on` (not the raw kind and resource id); each reminder links to My tasks; a reminder for a task that is no longer open (checked against live My tasks) is shown as read-only history, never as an action; bulk "Mark all read" calls `notification.read` once per row sequentially with progress and stops on the first error (no new backend).

- [ ] **Step 1: Write the failing browser tests:** HR sees both sections and the no-template notice; manager sees the progress for a report but no notes; the person sees only their view; set and clear probation date (version sent); rehire warning appears only with an open offboarding and the request is sent only after confirm; Home reminders grouped with `subject` text, link to My tasks, stale one is non-actionable, Mark all read sends N requests in order and stops on error; phone widths and themes.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** the file, node tests, lint, typecheck: PASS.
- [ ] **Step 5: Commit** `feat(console): process section on the profile, rehire warning, Home reminders`.

### Task 7: Docs and acceptance

**Files:**
- Modify: `docs/PROGRESS.md` (PR 3 entry above PR 2's, keep both), `docs/hr/PROGRESS.md`, `docs/ASSISTANT_RETEST.md` only if an assistant row changes (none expected), `docs/hr/SETUP.md` (one paragraph: how the HR owner creates the first templates and what staff will see)
- Create: `docs/hr/PROCESSES_RETEST.md` (the owner's manual acceptance rows on staging: create a template, add a synthetic person, see the tasks, complete, approve, send back, leave and rehire, reminders)

- [ ] **Step 1:** write the docs; run the lint for docs if the repo has one (`make lint` subset: find it in `Makefile`), keep lines within the repo wrap. **Step 2: Commit** `docs(hr): processes console notes and retest rows`. **PR 3 gate:** whole-branch review, then open the PR (staging only; migration 0636 stops all services briefly).
