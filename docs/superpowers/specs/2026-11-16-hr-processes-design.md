# HR phase 1: process templates, onboarding and offboarding, a better org chart (design)

Status: DRAFT for the owner's approval (written 2026-11-16; revised after the builder check, section 5.7; nothing is built).
Owner decisions so far (chat, 2026-11-16): HR for **corporate companies** (EPF, SOCSO, EIS, PCB), proper
onboarding, recruitment, custom workflows (interview and any other HR process), a proper org chart and proper
attendance; **approach C** (a small workflow core, then real consumers, in this order: processes with
onboarding and offboarding, recruitment, leave/claims/attendance, payroll); a template is **chosen by department,
employment type or legal entity, with ordered steps inside and no branching** inside a template; the result must
be **Malaysian grade** (defaults for Malaysian practice, Malay and English, PDPA, official sources). Attendance:
templates and rules first, clock-in later (that is phase 3, not this document).
Related: `2026-11-01-hr-people-design.md` (people, access), `2026-11-02-hr-owner-move-design.md`, memory
`hr-refinement-programme`. The reminder and notification layer is designed in the **Inbox refinement** session (migrations
0620-0629); this document names the seam (section 8) and does not build push delivery.

## 1. Intent

A new hire should arrive to a prepared list of things that must happen, each with an owner and a date, and so should a
leaver. Today nothing does that: a person is added, a leaver is marked leaving and finished by the worker, and every
other step (contract, statutory registrations, equipment, handover, final pay) lives in someone's head. A corporate HR
team needs the same steps done the same way every time, visible to HR, the manager and the person, with overdue work
showing. The same machinery is the "custom workflow" the owner asked for: any HR process is an ordered list of steps
with owners and due dates, and the later phases (interview rounds, claims, leave, payroll preparation) are processes
of the same shape.

## 2. Goals, non-goals, anti-goals

Goals
- A **process template**: a name, a kind (`onboarding`, `offboarding` or `custom`), a rule saying who it applies to
  (department, employment type, legal entity, each optional) and ordered steps.
- A **step**: title (Malay and English), description, owner role (HR team, the person's manager, the person, or one
  named employee), due date relative to an anchor (`start_date`, `last_day`, or `probation_end`, negative offsets
  allowed), required or optional, optional "needs approval by" (HR team or the manager), optional automatic
  completion check (section 5).
- A **process** per person, started automatically when a person is added (onboarding) or marked leaving (offboarding),
  or by HR on demand (`custom`), made from the best-matching template at that moment and **frozen** (editing a
  template never changes running processes).
- **My tasks** for anyone who owns a task, a **Processes** tab for HR with filters (kind, status, overdue, department,
  owner), and a progress section on the person's profile.
- **Malaysian default templates** the HR owner creates in one click, then edits (section 7).
- An **org chart** that is a real chart: boxes, collapsible branches, department colours, starting and leaving
  markers, a legal-entity and department filter, print and CSV export.
- Console reminders: new, due soon and overdue tasks on Home and My tasks (section 8).

Non-goals (phase 1)
- No branching or conditions **inside** a template (owner decision); several templates are chosen between by the rule.
- No recruitment, leave, claims, attendance or payroll (phases 2-4); no positions, vacancies or headcount in the
  chart (they arrive with recruitment openings in phase 2); no dotted-line or second manager.
- No push delivery (Telegram, email) of reminders: the Inbox session's layer provides it; no assistant cards for
  processes (they follow in a later slice; section 9).
- No statutory filing, payment or calculation. A statutory step is a **reminder with a reference**, never a filing.

Anti-goals
- A task list that silently skips a statutory step because a template was edited: a frozen process and an audit trail
  prevent it.
- Giving a manager rights over HR data because they own a task: a task row carries no personal data beyond the
  employee's name and the step text.
- An engine so general that nobody can explain what it does.

## 3. Constraints found in the code (read 2026-11-16, main f0f6b5bb)

1. A manager has **no HR rights of their own**: authority comes from `hr_lock_authority` and the switches
   (`approves_team`, `hr_team`, `manage_people`, `sensitive_details`, `directory`, ...). The manager is resolved only as
   the user whose linked employee equals `hr_employees.manager_id` (`hr_employee_accounts`); an unlinked or inactive
   manager resolves to nobody. A task for "the manager" must therefore fall back to the HR team when nobody resolves.
2. The leaving flow already ends access: `hr_finish_leaver` runs the day after `last_day` and deletes the leaver's
   grants, disables the membership and revokes sessions. A task owned by the leaver must be due on or before
   `last_day`; a task still open for the leaver after that is skipped by the system with a note.
3. There is **no scheduler for due dates**. The always-on worker loop `hr_leavers_round` (every 15 minutes,
   `apps/worker/main.py`) is the right place for a sibling round; the opt-in HR maintenance role is not wired in
   deployment and must not be relied on.
4. `hr_cases` is closed (four domains in a CHECK, typed payload, no assignee or due date, the distinct-reviewer rule
   only for payroll). Processes need **their own tables**; cases are not reused.
5. The people data has one manager per person, `department_id`, free-text `legal_entity_reference` and
   `employment_category`, `employment_type`, `start_date`, `last_day`; **no probation date**, no positions, no
   entity table. The org chart can only be a reports-to tree filtered by department and entity text.
6. Writes to HR rows go through definer functions (`csa_app` has no INSERT/UPDATE on `hr_employees`); HR tables are
   tenant-isolated with `ENABLE` (not `FORCE`) row level security and explicit grants; immutable tables use
   `hr_immutable_record`. The new tables follow the same pattern (migration lane 0630-0649 for this phase; the
   README lane table gets a line).
7. `hr_notifications` has a closed `kind` list (`workflow_review`, `training_review`, `interview_upcoming`) and no body or
   due date. Phase 1 extends it (section 8) rather than creating a second notification table.

## 4. Approaches considered

For where the work lives:
- **A. Reuse `hr_cases`.** Widening the four-domain CHECK, the typed payload and the grants, plus an assignee and due
  date, bends a closed approval model into a task list and risks the payroll distinct-reviewer rule. Rejected.
- **B. New tables for processes, kept small (chosen).** Templates, steps, process instances, task instances and an
  immutable event log, all tenant-isolated; cases stay as they are and later phases may create a process from a case.
- **C. A general workflow engine with branching.** Rejected by the owner for now.

For time-driven work (overdue marking, reminders):
- **Worker round beside `hr_leavers_round` (chosen):** always on, no per-tenant provisioning, same pattern.
- The opt-in maintenance role: not deployed. The browser timer on Home: only runs while the owner has the page open.

For who sees what:
- **Task owner sees their tasks, HR sees all, nobody else sees anything (chosen).** No dependence on `approves_team`:
  owning a task is the grant, and the row holds no sensitive data.

## 5. Design

### 5.1 Data model (migration lane 0630-0649)

- `hr_process_templates(client_id, id, kind, name, version, is_active, created_by, created_at, ...)`. `kind` in
  `onboarding`, `offboarding`, `custom`. Editing creates a new `version`; the old version stays referenced by running
  processes through their frozen copy.
- `hr_process_template_rules(template_id, department_id null, employment_type null, legal_entity_reference null,
  region null)`: a null means "any". **Matching:** among active templates of the kind whose set rules all match
  the person, pick the one with the most non-null rules; a tie is broken by the oldest template and is shown to the
  template author as a conflict warning, never guessed at runtime. A template with no rule is the company default.
- `hr_process_template_steps(template_id, ordinal, title_en, title_ms, description_en, description_ms, owner_role,
  owner_employee_id null, anchor, due_offset_days, required, approval_by null, auto_check null, reference_note)`.
  `owner_role` in `hr`, `manager`, `employee`, `person`. `anchor` in `start_date`, `last_day`, `probation_end`.
  `auto_check` in `account_linked`, `personal_details_complete`, `document_uploaded:<category>`, `access_removed`.
- `hr_processes(client_id, id, employee_id, kind, template_id, template_version, anchor_dates jsonb, status, started_by,
  started_at, closed_at)`: status `active`, `completed`, `cancelled`. One active process per (employee, kind) unless
  `custom`. A frozen copy of the template steps is stored on the process (rows below), not read back from the template.
- `hr_process_tasks(client_id, id, process_id, ordinal, title_en, title_ms, description_en, description_ms, owner_role,
  assignee_employee_id null, anchor, due_offset_days, due_on null, due_overridden, required, approval_by, status, completed_by, completed_at, approved_by,
  approved_at, note, version)`. Status `todo`, `awaiting_approval`, `done`, `rejected` (returns to `todo` with the
  reason in the log), `skipped`. `assignee_employee_id` is resolved when the task is created and re-resolved when the
  manager changes (a manager change rewrites open `manager` tasks and writes an event).
- `hr_process_events(client_id, id, process_id, task_id null, at, actor, action, detail)`: immutable
  (`hr_immutable_record`), every create, assign, complete, approve, reject, skip, cancel, template change and system action.
- `hr_employees` gains `probation_end date null` (set by HR on the person form; a process step anchored on it is created
  with its due date when the date is set, and moves when it changes).

### 5.2 Starting and ending processes

- `person.add` (existing definer path) and `person.rehire` start an **onboarding** process in the same transaction when a
  template matches; if none matches the person is added without one and HR sees "No onboarding template matches"
  on the person's profile and on Processes.
- `person.leave` starts an **offboarding** process in the same transaction; `person.leave_undo` cancels it
  (`cancelled`, open tasks closed, events written); changing `last_day` moves the due dates.
- `custom` processes are started by HR from the Processes tab for one person.
- The leaving worker (`hr_finish_leaver`) marks the system step `access_removed` done and the offboarding process
  `completed` when every required task is done or skipped; open tasks owned by the leaver are skipped with a note.

### 5.3 Who can do what

- **HR owner and `manage_people` holders:** create, edit, deactivate templates; start and cancel processes; see all
  processes and tasks; complete or skip any task on someone's behalf (event records the real actor); resolve approvals
  assigned to HR. Approval by the HR team is by any holder; **the person who completed a step cannot approve it**
  (reuse of the payroll distinct-reviewer idea).
- **Task assignee** (resolved employee, through `hr_employee_accounts`): sees and completes their own tasks, adds a
  note, sees the process name and the employee's name; nothing else of the person.
- **Manager:** sees processes of direct reports whose tasks are theirs or in the process (titles, status, due dates only).
- **Person:** sees their own tasks (the `employee` role) and progress; a person with no console account yet (before day
  one) has tasks completed by HR on their behalf.
- All writes go through definer functions with `expected_version`, idempotency keys and audit, in the same style as
  `hr_people_apply`. No new console switch is added.

### 5.4 Reminders and the scheduler

A new always-on worker round `hr_tasks_round` (900 s, registered beside `hr_leavers_round`, same per-tenant
transaction pattern) marks tasks `overdue` (derived from `due_on < today` in the company's region time zone), and writes
notification rows (section 8) for: assigned, due in 2 days, due today, overdue (once a day, at most 3 days, then weekly).
Rows are idempotent by `(subject, reminder_key)` as in `hr_notifications`.

### 5.5 Screens

- **Processes** tab (HR, `manage_people`, and managers for their reports): list with filters (kind, status, overdue only,
  department, owner), a process detail (steps as a timeline with owners, dates, status, who did what), start and cancel.
- **My tasks**: on Home for everyone who owns a task (a card with count and the next items), and a full list with
  complete / note / mark done; approval tasks show approve and send back.
- **Templates** (Settings, `manage_people`): list, editor (name, kind, who it applies to, steps with drag order, owner
  role, due offset with a plain sentence such as "5 days after the start date", required, approval), a preview
  "this applies to 6 people now" and conflict warnings, copy, deactivate. Malay and English fields side by side, English
  required, Malay optional (a missing Malay title falls back to English).
- **Person profile**: an "Onboarding" or "Offboarding" section with progress and the next task.
- **Org chart** (existing People tab view): a drawn tree with collapsible branches, department colour, "starting" and
  "leaving" markers, filters by department and legal entity, search, expand all, print, CSV. Same data endpoint, same
  loop warning; the left, unlinked and "no manager" states are shown rather than dropped.

### 5.6 API

Under `/console/v1/hr`: `templates` (list, get, create, update-as-new-version, deactivate), `processes` (list, get,
start, cancel), `tasks` (mine, get, complete, skip, approve, send back, note), `org-chart` (extended, additive fields).
Same envelope rules as the other HR routes (CSRF, `Idempotency-Key`, `expected_version`, problem codes); the OpenAPI
file and `docs/CONSOLE_API.md` are updated with each route.

### 5.7 Rulings after the builder check (my calls, marked; the owner may overrule any)

1. **"The HR team"** for tasks and approvals means the HR owner plus every holder of `manage_people`. (The older
   `hr_team` switch is not used for processes.) An `hr` task sits in a shared pool: any member may complete it, and
   a reminder is written **per pool member** (dedupe key includes the recipient).
2. **Approval deadlock:** see section 10, question 1 (owner decision). Until decided: the HR owner may approve a step
   they completed themselves **only when no other eligible approver exists**, and the event is recorded as
   `approved_without_second_person`.
3. **A step anchored on a date that is not set** (`probation_end` null) exists with `due_on` null, shows "waiting for a
   date" on the profile and in the process, is never overdue, and counts as an open required step for completion.
4. **Completion:** an onboarding or offboarding process completes when every required task is `done` or `skipped`;
   open optional tasks are then set to `skipped` by the system. HR may **close anyway** with a note (audited).
5. **Existing data:** no backfill. People already employed get no onboarding; people already marked leaving get no
   offboarding. HR can start a process for an existing person by hand.
6. **Due dates are stored per task** with their `anchor` and `due_offset_days`. A change of `last_day`, `start_date`
   or `probation_end` recomputes open tasks whose `due_overridden` is false. HR may change one task's due date
   or assignee (sets `due_overridden`, audited).
7. **A leaver-owned task whose offset falls after `last_day`:** the editor warns when the template is saved; at creation
   the due date is clamped to `last_day` with a note; the leaving worker still skips anything left open.
8. **Matching:** department by id (no parent coverage); employment type exact; legal entity and category compared
   trimmed and case-insensitive. The editor offers the values that already exist as a pick list (free text still
   allowed) so a typo does not hide a template.
9. **Reminders for people without a console account** go to the HR pool. The Home card and My tasks read **live
   task queries**; `hr_notifications` rows are only the alerts (assigned, due soon, due, overdue).
10. **The review gate** blocks only the "create the Malaysian default templates" action in PR 4; the org chart ships
    regardless. The builder gathers the official sources and timings during implementation; the owner (and the
    practitioner the owner names) reviews before the action is enabled.

## 6. Malaysian grade: how it is enforced

- **Defaults, not law in code.** The statutory steps are data in editable default templates. No rate, threshold or
  date lives in Python. Each default step has a `reference_note` naming the official body and form
  (for example the EPF employer portal, PERKESO registration, the LHDN new-employee notification) and **no figures**;
  the timing of each ("within N days") is entered at implementation from the official page and recorded in the step with
  its source and the date it was checked. Nothing in this document is a statement of the law.
- **Malay and English** for every default title and description; Malay written for HR practice, reviewed by the owner
  (same review route as the Malay write facts).
- **PDPA:** a template step may reference personal data only by category; processes store no IC, tax, EPF or bank numbers
  (those stay in the encrypted personal details); the process audit trail follows the same retention as the person.
- **Regions:** the person's region (Peninsular/Labuan, Sabah, Sarawak) is a template rule and the due-date calendar uses
  the region's time zone; public holidays are phase 3.
- **Review gate:** the default templates ship only after the owner (and ideally an HR practitioner or accountant the owner
  names) has read the English and Malay text and the step timings; until then they are not created.

## 7. Malaysian default templates (content to be verified at build time)

Onboarding (owner roles in brackets, offsets from `start_date` unless stated): signed employment contract and offer
on file (HR, before start); personal details completed by the employee (person, first week, auto-check); bank and
statutory numbers recorded (person/HR, auto-check on personal details); EPF, SOCSO and EIS registration or update (HR);
LHDN new-employee notification and tax file (HR); workplace equipment and accounts (manager); orientation and policies
signed (HR); manager's welcome and 30-day goals (manager); probation review (manager then HR, anchored on `probation_end`).
Offboarding (offsets from `last_day`): resignation or termination letter on file (HR); notice period and handover plan
(manager); knowledge and files handover (person); return of equipment (person); final pay and leave balance (HR,
payroll phase later); statutory closure steps for EPF and tax (HR); exit conversation (manager/HR); access removed
(system). Each step names its source in `reference_note` after the verification pass above.

## 8. Reminders: the seam with the Inbox session

Phase 1 writes tasks' reminders as `hr_notifications` rows with new kinds `task_assigned`, `task_due_soon`,
`task_due`, `task_overdue` (the CHECK is extended by reading the live list, as migration 0531 does) and shows them on
Home and My tasks. All writing goes through one small module `csa/hr/reminders.py` with one function
`emit(conn, client_id, event, recipient_employee_id, dedupe_key, due_on, subject)`; when the Inbox session's generic
reminder and notification layer is merged, `emit` forwards to it (so Telegram, email and quiet hours apply) and the
console list reads from it. Nothing else in the HR code knows about delivery. **If the layer has not merged by build
time, phase 1 ships with console-only reminders** (owner to confirm, section 10).

## 9. Assistant

Out of this slice: no new proposal or plan kinds (they need a CHECK change on the live list and an executor). The
assistant already knows the HR tab as a page; a read-only answer "what is outstanding for X's onboarding" and a
"mark done" card follow once the tables exist (slice 1c, own small PR).

## 10. Owner questions (answer or accept my default)

1. **A step needs approval but the only possible approver is the person who did it** (a one-person HR team, or an
   unlinked manager that falls back to HR). Options: (a) the HR owner may approve their own step when nobody else
   can, recorded as such in the audit trail (my default); (b) never, a second person is required (the process then
   waits); (c) tenant admin override. Default: (a).
2. **Reminders console-only in phase 1** if the Inbox layer is not merged yet? Default: yes, with the seam above.
3. **Probation end date** as a new person field, set by HR (default: yes, optional).
4. **Positions and vacancies** in the org chart with recruitment (phase 2) rather than here (default: yes, phase 2).
5. **New hires without a console account:** HR completes their tasks; a later slice lets the invitation flow give them
   their own task list (default: later).

## 11. Build plan (4 PRs, subagent-driven, each reviewed)

1. **Data and engine** (migrations 0630-0634): tables, RLS and grants, definer functions, template matching,
   process creation and freeze, hooks in `person.add`, `person.rehire`, `person.leave`, `person.leave_undo`,
   `hr_finish_leaver`, events, probation_end. Tests: RLS both ways, matching and ties, frozen copy, hook atomicity,
   offboarding timing against the leaver job, undo cancels.
2. **Tasks, approvals and the round**: task actions, approval and distinct-reviewer, assignee re-resolution, `hr_tasks_round`,
   `csa/hr/reminders.py`, notification kinds, API routes and docs.
3. **Console**: Processes tab, My tasks (Home card + list), person section, template editor, tests (unit and browser,
   phone width), Malay and English copy.
4. **Malaysian defaults and org chart**: default template creation action (after the review gate), org chart drawing,
   filters, print and CSV.
Rollout is staging only; migrations stop all services; the production order rule in `deploy/PRODUCTION.md` applies.

## 12. Risks and review focus

- A person added with no matching template (clear message, not silence); two templates tying (conflict warning).
- Manager changes mid-process (open manager tasks follow the new manager; done tasks stay).
- Leaving date moved earlier than done or due tasks (due dates recompute; tasks already done stay done).
- Rehire after offboarding (new onboarding, old processes stay closed).
- An unlinked or inactive manager (task falls to the HR team with a visible note).
- A task completed by HR on someone's behalf (audited with the real actor; the person sees "done by HR").
- Cross-tenant reads of templates and tasks (RLS tests on every new table, as for `media_assets`).
- Overdue noise: the reminder cadence caps and dedupe keys are tested.
