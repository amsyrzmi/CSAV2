# HR processes, backend (PRs 1-2): templates, instances, tasks, approvals, reminders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A company defines HR process templates; adding, rehiring or marking a person leaving starts a frozen process of ordered tasks with owners, due dates, approvals and reminders; everything is readable and actionable through tenant-isolated API routes.

**Architecture:** New tables, a trigger on `hr_employees` that starts, re-dates and ends processes (so `hr_people_apply` and `hr_finish_leaver` are not edited), definer functions for every write, a small Python service and routes in the style of the existing HR API, and one worker round beside `hr_leavers_round`. The console (PR 3) and the Malaysian defaults with the org chart (PR 4) get their own plan.

**Tech Stack:** Postgres (RLS, SECURITY DEFINER functions, triggers), FastAPI, pydantic, psycopg, pytest with the `HrWorld` fixture (`tests/test_hr_people.py`).

**Spec:** `docs/superpowers/specs/2026-11-16-hr-processes-design.md` (approved by the owner 2026-11-16, approval-deadlock option (a), the other four defaults accepted). Sections 5.1-5.7 and 8 are binding; section 5.7 are the owner-accepted rulings.

## Plan decisions (mine; the spec is silent)

- **Trigger, not an edit of `hr_people_apply`:** an `AFTER INSERT OR UPDATE` trigger function `hr_process_on_employee()` (SECURITY DEFINER, pinned search_path) on `hr_employees` starts onboarding (insert; `is_active` false to true), starts offboarding (`last_day` null to a date), cancels it (`last_day` back to null), recomputes open task dates (`start_date`, `last_day` change), re-resolves open `manager` tasks (`manager_id` change) and closes the leaver's open tasks (`is_active` true to false). It runs inside the writer's transaction, so it covers `hr_people_apply`, `hr_finish_leaver` and any future writer, and a failure rolls the person write back.
- **Probation date** is written by a new definer function `hr_probation_set(p_client, p_key, p_fingerprint, p_body)` reached through a new HR operation `person.probation_set` (`{employee_id, expected_version, probation_end | null}`), not by editing `hr_people_apply` or `hr_people_rows`. The date is read back through the processes API (`GET /hr/people/{employee_id}/processes` returns the anchor dates).
- **Templates are edited in place** (`version` + 1, steps replaced); running processes are unaffected because their tasks are frozen copies and carry `template_id` and `template_version` for the record. Each template change writes a `console_audit` row.
- **Time zone** for "today" and due dates: `Asia/Kuala_Lumpur`, as `hr_leavers_due` does (Sabah and Sarawak share UTC+8).
- **Files:** `db/migrations/0630`-`0635`, `db/sql/hr/process_*.sql` for read queries, `csa/hr/process_models.py`, `csa/hr/processes.py` (service), `csa/hr/process_routes.py` (`register_process_routes(router, session_dep, service_dep)` called from `build_hr_router`), `csa/hr/reminders.py`, `apps/worker/main.py` (round). Tests: `tests/test_hr_process_*.py`.

## Global Constraints

- Public repo: synthetic data only; never read `/srv/csa-staging/secrets`.
- Every new table: `ENABLE` (not `FORCE`) row level security with the tenant policy on `app.client_id`, `REVOKE ALL` from `PUBLIC`, `csa_app`, `csa_ro`, `csa_identity`, `csa_hr_maintenance`, then `GRANT SELECT` to `csa_app` only (no table write grants); all writes through definer functions with `SET search_path=pg_catalog,public,pg_temp` and `EXECUTE` granted to `csa_app` only; `hr_process_events` is immutable through `hr_immutable_record`. Copy the exact pattern of `0521_hr_people_records.sql`.
- Write functions take the tenant's operations lock first (`pg_advisory_xact_lock(hashtextextended(p_client::text||':hr-operations',0))`) as `hr_people_apply` and `hr_finish_leaver` do, check authority the same way (`hr_is_owner` / `hr_can_manage_people`), and use `p_key` and `p_fingerprint` for idempotency exactly as `hr_people_apply` does (read it first).
- HR team = the HR owner plus every holder of `manage_people` (spec 5.7.1). A task is visible and actionable by its assignee regardless of switches; a manager sees processes of direct reports (titles, status, dates only).
- A person who completed a task cannot approve it; the one exception is the HR owner when no other eligible approver exists, recorded as event `approved_without_second_person` (spec 5.7.2, owner option (a)).
- Migrations from 0630 only; `EXPECTED_SCHEMA_VERSION` (`apps/gateway/main.py`) becomes the highest merged version in each PR; add the lane line to `db/migrations/README.md`. A constraint change on `hr_notifications.kind` reads the live list with `pg_get_constraintdef` (method of 0531/0540/0611), never a fixed list.
- CI runs `uv run pyright` on the WHOLE project including tests: run it on every changed Python file (`--pythonpath /home/amsyrzmi/CSAV2/.venv/bin/python`), plus `ruff check`, `ruff format --check`, `lint-imports`. Run test files one at a time; DB tests need Docker.
- Docs per PR: `docs/CONSOLE_API.md` rows for each route, `docs/console_openapi.json` regenerated with `python -m scripts.export_console_openapi`, a `docs/PROGRESS.md` entry.
- No model calls, no customer-facing behaviour, no push delivery (spec non-goals).

## Review Focus

- Cross-tenant: company A's templates, processes, tasks and events unreadable and unwritable by company B on every new table and function (RLS tests in both directions, plus a definer function called with another tenant's ids).
- A person added or rehired with no matching template: no error, no process, and the processes read says so.
- Two templates tying on specificity: the author gets a conflict warning at save; runtime picks the oldest and never errors.
- Leaver timing: tasks owned by the leaver due after `last_day` are clamped; open leaver tasks are skipped when the leaver finishes; `person.leave_undo` cancels the offboarding and a later `person.leave` starts a fresh one.
- Manager changes or an unlinked manager mid-process: open `manager` tasks follow; with no resolvable manager the task goes to the HR pool with a note.
- Replaying any write with the same key changes nothing; a stale `expected_version` is refused.

---

## PR 1: schema, templates, matching, instantiation, reads

### Task 1: Schema (migration 0630)

**Files:**
- Create: `db/migrations/0630_hr_processes.sql`, `tests/test_hr_process_schema.py`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION`), `db/migrations/README.md` (lane line `0630`-`0649`: HR processes)
- Test: `tests/test_hr_process_schema.py`

**Interfaces:**
- Produces tables (all `client_id uuid NOT NULL REFERENCES clients`, composite keys `(client_id, id)`): `hr_process_templates(id, kind, name, version, is_active, created_at, updated_at)`; `hr_process_template_rules(template_id, department_id null, employment_type null, legal_entity_reference null, region null)`; `hr_process_template_steps(template_id, ordinal, title_en, title_ms null, description_en null, description_ms null, owner_role, owner_employee_id null, anchor, due_offset_days, required, approval_by null, auto_check null, reference_note null)`; `hr_processes(id, employee_id, kind, template_id, template_version, anchor_dates jsonb, status, started_by null, started_at, closed_at null)`; `hr_process_tasks(id, process_id, ordinal, title_en, title_ms, description_en, description_ms, owner_role, assignee_employee_id null, anchor, due_offset_days, due_on null, due_overridden, required, approval_by null, auto_check null, status, completed_by null, completed_at null, approved_by null, approved_at null, note null, version)`; `hr_process_events(id, process_id, task_id null, at, actor null, action, detail jsonb)`; `hr_employees.probation_end date null`.
- CHECKs: `kind in ('onboarding','offboarding','custom')`; `owner_role in ('hr','manager','employee','person')` and `owner_employee_id` required exactly when `owner_role='person'`; `anchor in ('start_date','last_day','probation_end')`; `approval_by in ('hr','manager')`; `auto_check in ('account_linked','personal_details_complete','access_removed')` or `document_uploaded:<category>` (a CHECK on the prefix); `status in ('todo','awaiting_approval','done','rejected','skipped')`; process `status in ('active','completed','cancelled')`; `due_offset_days between -365 and 365`; step ordinal unique per template. Unique: one `active` process per `(client_id, employee_id, kind)` where `kind <> 'custom'` (partial unique index). Index `hr_process_tasks (client_id, assignee_employee_id, status, due_on)` and `(client_id, process_id, ordinal)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_hr_process_schema.py` (disposable database, copy the style of `tests/test_hr_persistence.py` / `tests/test_media_schema.py`): `test_tables_exist_with_rls_and_only_select_for_the_app_role` (every new table has RLS enabled, not forced, and `csa_app` holds SELECT only; `csa_ro` nothing); `test_row_security_isolates_tenants_on_every_table` (insert as owner for two tenants; as `csa_app` with `app.client_id` set to A, B's rows are invisible and a direct INSERT is refused); `test_events_are_immutable` (UPDATE and DELETE on `hr_process_events` raise); `test_checks_reject_bad_values` (one case per CHECK above, including owner_role/person pairing and the partial unique index allowing two `custom` processes and refusing two active `onboarding`); `test_probation_end_column_is_a_date_and_nullable`.
- [ ] **Step 2: Run** `tests/test_hr_process_schema.py`: FAIL. **Step 3: Implement** the migration (IF NOT EXISTS where the repo's style needs it, rollback notes in the header comment as in 0540/0611) and the two small edits. **Step 4: Run** the file, `tests/test_migrate.py`, `tests/test_health.py`, `tests/test_bookings_bot_schema.py`: PASS.
- [ ] **Step 5: Commit** `feat(hr): process tables`.

### Task 2: Template functions and matching (migration 0631)

**Files:**
- Create: `db/migrations/0631_hr_process_templates.sql`, `tests/test_hr_process_templates.py`
- Test: `tests/test_hr_process_templates.py`

**Interfaces:**
- Produces: `hr_template_apply(p_client uuid, p_key uuid, p_fingerprint text, p_body jsonb) RETURNS jsonb` with `p_body.operation` in `template.create` (`{kind, name, rules, steps}`), `template.update` (`{template_id, expected_version, name, rules, steps, is_active}`; version + 1, steps and rules replaced), `template.deactivate` (`{template_id, expected_version}`); result `{template_id, version, conflicts: [template_id...], warnings: [...]}`. `hr_template_rows(p_client uuid) RETURNS TABLE(...)` and `hr_template_get(p_client uuid, p_template uuid) RETURNS jsonb` (HR team only). `hr_process_match(p_client uuid, p_employee uuid, p_kind text) RETURNS uuid` (null when none), internal and also callable by the trigger.
- Validation (all `RAISE EXCEPTION` with stable codes like the existing functions): 1-40 steps, English title 1-160 chars, Malay optional, offsets within ±365, `onboarding` steps anchor `start_date` or `probation_end`, `offboarding` steps anchor `last_day`, `custom` any; `owner_role='person'` needs an active employee of this tenant. **Warnings, not errors:** a `person`/`employee`-role step in an offboarding template whose offset is after 0 (spec 5.7.7); two active templates of the same kind with equal specificity and overlapping rules (listed in `conflicts`).
- Matching (spec 5.1 and 5.7.8): among active templates of the kind whose set rules all match the employee (department by id, employment type exact, legal entity and category trimmed and case-insensitive, region exact), highest count of non-null rules wins; ties go to the oldest `created_at`; a template with no rule is the default. Authority: HR owner or `manage_people` only.

- [ ] **Step 1: Write the failing tests:** `test_create_update_deactivate_template_bumps_version_and_writes_audit` (`console_audit` row per change); `test_steps_and_rules_validated` (parametrised bad inputs, one code each); `test_matching_picks_most_specific_then_oldest` (default, department, department+type, tie); `test_matching_is_trimmed_and_case_insensitive_for_entity`; `test_no_match_returns_null`; `test_tie_is_reported_as_conflict_at_save`; `test_offboarding_late_person_step_warns`; `test_non_hr_and_other_tenant_are_refused` (a staff member without `manage_people`, and a call naming another tenant's template id); `test_same_key_replay_changes_nothing_and_stale_version_is_refused`.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement** the SQL (pattern of `hr_people_apply` for lock, authority, key and fingerprint). **Step 4: Run** the file and `tests/test_hr_process_schema.py`: PASS.
- [ ] **Step 5: Commit** `feat(hr): process templates and matching`.

### Task 3: Starting, freezing and ending processes (migration 0632)

**Files:**
- Create: `db/migrations/0632_hr_process_instances.sql`, `tests/test_hr_process_instances.py`
- Test: `tests/test_hr_process_instances.py`, `tests/test_hr_people.py`, `tests/test_hr_people_records.py` (existing tests must still pass)

**Interfaces:**
- Produces: internal `hr_process_start(p_client uuid, p_employee uuid, p_kind text, p_template uuid, p_actor uuid) RETURNS uuid` (null when no template matches and `p_template` is null; copies the template's steps into `hr_process_tasks`, computes `anchor_dates` and each `due_on = anchor_date + due_offset_days`, null when the anchor date is null; resolves each task's assignee: `manager` to `hr_employees.manager_id` when that employee is active and linked in `hr_employee_accounts`, else null with `owner_role` kept and event detail `{fallback:'hr_pool', reason}`; `employee` to the person; `person` to `owner_employee_id`; `hr` stays a pool (assignee null); writes one `process.started` event and one per task `task.created`); the trigger function `hr_process_on_employee()` described in Plan decisions; `hr_process_start_apply(p_client, p_key, p_fingerprint, p_body)` for HR starting a `custom` (or manual onboarding for an existing person) process: `{employee_id, kind, template_id}`; `hr_process_cancel_apply(p_client, p_key, p_fingerprint, p_body)`: `{process_id, expected_version?}` (processes carry no version column: use the process `status` as the guard); `hr_probation_set` (Plan decisions) and the operation name `person.probation_set` in `csa/hr/automation_models.py` / `operations.py`.
- Rules: leaver-owned (`employee`/`person` role where the person is the leaver) offboarding task with `due_on` after `last_day` is clamped to `last_day` at creation with event detail `{clamped:true}`; a change of `start_date`, `last_day` or `probation_end` recomputes open tasks with `due_overridden=false`; `is_active` true to false skips the leaver's open tasks (note "person left") and completes the offboarding process when every required task is `done` or `skipped` (the system auto-checks `access_removed` as done at that moment); `last_day` back to null cancels the active offboarding process (open tasks `skipped`, event `process.cancelled`); a manager change rewrites open `manager` tasks and writes events; probation date set later creates no new tasks but fills `due_on` of tasks anchored on it (spec 5.7.3).
- Completion rule (spec 5.7.4) lives in one function `hr_process_settle(p_client, p_process)` that the later task actions also call: completes when all required tasks are `done`/`skipped`, then sets open optional tasks `skipped`.

- [ ] **Step 1: Write the failing tests** (HrWorld): `test_adding_a_person_starts_onboarding_from_the_matching_template`; `test_no_matching_template_adds_the_person_and_starts_nothing`; `test_process_is_a_frozen_copy` (edit the template afterwards, tasks unchanged); `test_task_assignees_resolved` (manager linked/active, manager unlinked falls to pool with the fallback detail, person role, employee role); `test_rehire_starts_a_new_onboarding_and_old_ones_stay_closed`; `test_marking_leaving_starts_offboarding_and_undo_cancels_it`; `test_leaver_task_after_last_day_is_clamped`; `test_changing_last_day_recomputes_open_tasks_but_not_overridden_ones`; `test_manager_change_moves_open_manager_tasks_only`; `test_probation_date_fills_due_dates` and `test_probation_set_rejects_stale_version_and_non_hr`; `test_finishing_the_leaver_skips_open_leaver_tasks_and_completes_offboarding` (run `hr_finish_leaver` after `last_day`); `test_custom_process_started_by_hr_and_two_custom_allowed`; `test_cancel_closes_open_tasks`; `test_other_tenant_cannot_start_or_cancel`.
- [ ] **Step 2: Run** the new file: FAIL; **Step 3: Implement**; **Step 4: Run** the new file plus `tests/test_hr_people.py` and `tests/test_hr_people_records.py`: PASS (the trigger must not change any existing result).
- [ ] **Step 5: Commit** `feat(hr): start and end processes from person changes`.

### Task 4: Reads and visibility (migration 0633)

**Files:**
- Create: `db/migrations/0633_hr_process_reads.sql`, `db/sql/hr/process_*.sql` as needed, `tests/test_hr_process_reads.py`
- Test: `tests/test_hr_process_reads.py`

**Interfaces:**
- Produces definer read functions (return rows only the caller may see; `EXECUTE` to `csa_app`): `hr_process_rows(p_client uuid, p_kind text, p_status text, p_overdue boolean, p_department uuid, p_owner uuid, p_limit int, p_after uuid)` (keyset, newest first); `hr_process_get(p_client uuid, p_process uuid) RETURNS jsonb` (process, tasks, events); `hr_task_rows_mine(p_client uuid, p_status text, p_limit int, p_after uuid)` (tasks where the caller's linked employee is the assignee, plus, for HR team members, unassigned `hr`-role pool tasks); `hr_person_processes(p_client uuid, p_employee uuid) RETURNS jsonb` (the person's processes plus `anchor_dates`, including `probation_end`, and "no template matches" flag per kind).
- Visibility (spec 5.3): HR team sees everything; a manager sees processes of direct reports and, within them, only titles, status, due dates, owner role and assignee name (no notes, no events detail); an assignee sees their own tasks and the process name and employee name; anyone else gets nothing (empty set or `hr_process_unavailable`, never another tenant's data).

- [ ] **Step 1: Write the failing tests:** `test_hr_team_sees_all_and_filters_work` (kind, status, overdue, department, owner, keyset paging); `test_manager_sees_direct_reports_processes_without_notes`; `test_assignee_sees_only_own_tasks`; `test_pool_tasks_listed_for_hr_team_only`; `test_stranger_and_read_only_see_nothing`; `test_other_tenant_sees_nothing`; `test_person_processes_report_no_matching_template`.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** the file: PASS.
- [ ] **Step 5: Commit** `feat(hr): process reads and visibility`.

### Task 5: Python models, service and routes for templates and processes

**Files:**
- Create: `csa/hr/process_models.py`, `csa/hr/processes.py`, `csa/hr/process_routes.py`, `tests/test_hr_process_api.py`
- Modify: `csa/hr/api.py` (call `register_process_routes` inside `build_hr_router`), `csa/hr/automation_models.py` and `csa/hr/operations.py` (`person.probation_set`), `docs/CONSOLE_API.md`, `docs/console_openapi.json`, `docs/PROGRESS.md`
- Test: `tests/test_hr_process_api.py`, `tests/test_console_contract_snapshot.py`, `tests/test_hr_operations.py`

**Interfaces:**
- Consumes: the Task 2-4 SQL functions; the existing `HrSession`, `HrService`, `tx` helpers, CSRF, `Idempotency-Key` and `expected_version` conventions of `csa/hr/api.py` (read `POST /operations` and `POST /workflows` first and match them).
- Produces routes under `/console/v1/hr` (tenant query like the other HR routes): `GET /templates`, `GET /templates/{id}`, `POST /templates` (create), `PUT /templates/{id}` (update, body carries `expected_version`), `POST /templates/{id}/deactivate`; `GET /processes` (filters from Task 4), `GET /processes/{id}`, `POST /processes` (start a custom or manual onboarding), `POST /processes/{id}/cancel`; `GET /people/{employee_id}/processes`. Pydantic models in `process_models.py` use `HrModel` (extra forbidden) with typed enums for kind, owner role, anchor, status, `approval_by`; every database refusal code maps to a stable problem code and sentence in the style of `csa/hr/operations.py` (no raw database text).
- Docs: one `CONSOLE_API.md` row per route and a short section; `console_openapi.json` regenerated.

- [ ] **Step 1: Write the failing tests** (HrWorld + the router, as `tests/test_hr_people_records.py`): `test_template_crud_round_trip_and_validation_errors_are_stable_codes`; `test_start_and_cancel_process_routes`; `test_probation_operation_via_operations_route`; `test_every_route_requires_csrf_and_idempotency_where_it_writes`; `test_non_hr_gets_403_and_other_tenant_404`; `test_responses_never_carry_database_text`.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** `tests/test_hr_process_api.py`, `tests/test_console_contract_snapshot.py`, `tests/test_hr_operations.py`, then `ruff`, `pyright`, `lint-imports` on changed files: PASS.
- [ ] **Step 5: Commit** `feat(hr): process templates and processes API`. **PR 1 gate:** whole-branch review, then open the PR (staging only; migration 0630-0633 stops all services).

---

## PR 2: tasks, approvals, reminders, the round

### Task 6: Task actions and approvals (migration 0634)

**Files:**
- Create: `db/migrations/0634_hr_task_actions.sql`, `tests/test_hr_process_tasks.py`
- Test: `tests/test_hr_process_tasks.py`

**Interfaces:**
- Produces `hr_task_apply(p_client uuid, p_key uuid, p_fingerprint text, p_body jsonb) RETURNS jsonb`, `p_body.operation` in `task.complete` (`{task_id, expected_version, note?}`), `task.skip` (`{..., reason}`, HR team only), `task.approve` (`{task_id, expected_version}`), `task.send_back` (`{..., reason}`), `task.note` (`{task_id, note}`), `task.reschedule` (`{task_id, expected_version, due_on}`, HR team only; sets `due_overridden`), `task.reassign` (`{task_id, expected_version, assignee_employee_id}`, HR team only; the target must be an active employee of the tenant), `process.close_anyway` (`{process_id, note}`, HR team only). Each writes one `hr_process_events` row with the real actor and calls `hr_process_settle`.
- Rules: only the assignee, or an HR team member (for `hr` pool tasks and on behalf of anyone), may complete; a task with `approval_by` goes to `awaiting_approval` on complete, and `approve` is by the approver role (HR team, or the manager of the person); **the actor who completed it cannot approve**, except the HR owner when no other HR team member or manager can (event `approved_without_second_person`); `send_back` returns the task to `todo` and keeps the reason in the event; automatic checks (`account_linked`, `personal_details_complete`, `document_uploaded:<category>`) complete a task when `task.complete` is attempted and the check passes, and refuse with `check_not_met` when it does not (HR may skip); `access_removed` is completed only by the system; completed, skipped and cancelled-process tasks refuse further actions (`task_closed`).

- [ ] **Step 1: Write the failing tests:** `test_assignee_completes_own_task_and_hr_completes_for_the_person` (event actor is the real one); `test_pool_task_any_hr_member_completes`; `test_approval_flow_and_send_back`; `test_completer_cannot_approve_but_sole_hr_owner_can_with_the_flag`; `test_manager_approval_falls_to_hr_when_no_manager_resolves`; `test_auto_checks_pass_and_fail` (personal details and a document of a category); `test_reschedule_and_reassign_set_override_and_survive_a_last_day_change`; `test_skip_and_close_anyway_are_hr_only_and_settle_the_process`; `test_closed_tasks_refuse_actions`; `test_stale_version_and_replayed_key`; `test_other_tenant_and_stranger_refused`.
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** the file and `tests/test_hr_process_instances.py`: PASS.
- [ ] **Step 5: Commit** `feat(hr): task actions and approvals`.

### Task 7: Task routes

**Files:**
- Create: `tests/test_hr_task_api.py`
- Modify: `csa/hr/process_models.py`, `csa/hr/processes.py`, `csa/hr/process_routes.py`, `docs/CONSOLE_API.md`, `docs/console_openapi.json`
- Test: `tests/test_hr_task_api.py`, `tests/test_console_contract_snapshot.py`

**Interfaces:**
- Produces: `GET /tasks/mine` (Task 4 `hr_task_rows_mine`), `POST /tasks/{task_id}/complete|skip|approve|send-back|note|reschedule|reassign`, `POST /processes/{process_id}/close`. Same conventions and error mapping as Task 5; the body models mirror the Task 6 operations.
- [ ] **Step 1: Write the failing tests:** one happy path per route through the router, the 403/404 matrix (assignee, manager, stranger, read-only, other tenant), CSRF and key requirements, stable error codes. **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** both files plus `ruff`, `pyright`, `lint-imports`: PASS. **Step 5: Commit** `feat(hr): task routes`.

### Task 8: Reminders and the worker round (migration 0635)

**Files:**
- Create: `db/migrations/0635_hr_task_reminders.sql`, `csa/hr/reminders.py`, `tests/test_hr_task_reminders.py`
- Modify: `apps/worker/main.py` (register `hr_tasks_round` beside `hr_leavers_round`, line ~810), `docs/PROGRESS.md`, `docs/hr/PROGRESS.md`
- Test: `tests/test_hr_task_reminders.py`, `tests/test_worker_hr_leavers.py` if one exists (find the existing leavers-round test and extend its pattern)

**Interfaces:**
- Produces: migration extends the `hr_notifications.kind` CHECK with `task_assigned`, `task_due_soon`, `task_due`, `task_overdue` by reading the live list (as 0540/0611); `hr_tasks_due(p_client uuid DEFAULT NULL)` (definer, every tenant for the worker, or the session's tenant) returning open tasks needing a reminder today with the recipient pool; `csa/hr/reminders.py` `async def emit(conn, client_id: UUID, event: Literal["task_assigned","task_due_soon","task_due","task_overdue"], recipient_employee_id: UUID, dedupe_key: str, due_on: date | None, subject: str) -> bool` (inserts one idempotent `hr_notifications` row per `(subject, reminder_key)`; returns False when it already existed) and `async def hr_tasks_round(...)` calling it; the round runs every 900 s, always on, one tenant per transaction like `hr_leavers_round`. Cadence (spec 5.4): assigned (once, when a task gets an assignee or enters the pool), due in 2 days, due today, overdue once a day for 3 days then weekly. HR pool tasks fan out one row per pool member (dedupe key includes the recipient); tasks for a person without a console account go to the pool.
- Seam (spec 8): all writes go through `emit`; nothing else in HR knows about delivery.

- [ ] **Step 1: Write the failing tests:** `test_constraint_extended_keeps_every_earlier_kind` and `test_rerun_of_0635_changes_nothing`; `test_round_writes_each_reminder_once_per_day` (run twice, one row); `test_cadence_due_soon_due_overdue_daily_then_weekly` (move the clock via the function's date argument or `now()` mocking the way existing time tests do); `test_pool_task_fans_out_per_member`; `test_unlinked_person_reminders_go_to_the_pool`; `test_done_or_cancelled_tasks_get_no_reminder`; `test_round_is_registered_and_runs_per_tenant` (follow the leavers-round test).
- [ ] **Step 2: Run:** FAIL. **Step 3: Implement. Step 4: Run** the new files, `tests/test_hr_task_api.py`, then `ruff`, `pyright`, `lint-imports`: PASS.
- [ ] **Step 5: Commit** `feat(hr): task reminders and the worker round`. **PR 2 gate:** whole-branch review, PROGRESS entry, `EXPECTED_SCHEMA_VERSION` `0635`, open the PR (staging only).
