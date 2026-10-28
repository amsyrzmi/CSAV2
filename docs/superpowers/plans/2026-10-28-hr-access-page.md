# HR Access Page ("Who can do what") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give a client's HR owner a "Who can do what" page under People & HR. It shows every console member's HR access as plain switches, lets the owner change one or many people safely, and warns before a change leaves work with nobody to do it.

**Architecture:**
- **Migration 0482** adds two owner-only, security-definer read functions: everyone's grant rows, and the reporting lines.
- **A pure Python module** (`csa/hr/access_switches.py`) owns the switch table, presets, state, warnings and the version stamp.
- **A service and three routes** (`csa/hr/access_page.py`, in the existing `/console/v1/hr` router) read pages of people, apply batched changes through the existing `HrOperations.apply` path, and read recent changes.
- **The Next.js page** (layout B: people list plus a detail panel) shows what the server returns. The old forms move under "Advanced".

**Tech Stack:** PostgreSQL 16, psycopg 3 async, FastAPI, Pydantic v2, pytest with Testcontainers, Next.js with TypeScript, `node --test`, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-28-hr-access-page-design.md` (owner-approved 2026-10-28).

## Global Constraints

**No new authority machinery**
- No new console roles, no new tables, no RLS policy changes, and never FORCE RLS.
- Every access write goes through `HrOperations.apply` (`csa/hr/operations.py`), so authorization, receipts and `hr_authority_events` stay unchanged.

**Migration**
- The migration file is `db/migrations/0482_hr_access_summary.sql`.
- `apps/gateway/main.py` gets `EXPECTED_SCHEMA_VERSION = "0482"`. Tests import it and never hardcode it.
- The functions are `SECURITY DEFINER`, use `SET search_path=pg_catalog,public,pg_temp`, and return nothing unless `public.hr_is_owner(p_client)`.
- `EXECUTE` goes to `csa_app` only, revoked from `PUBLIC`, `csa_ro` and `csa_identity`.

**Owner-only routes**
- Non-owners get the same response other owner-only HR reads give today: `HrPolicyError("permission_denied")`, which `hr_http_error` in `csa/hr/api.py` maps to 404 `resource_unavailable`, as `/console/v1/hr/records/employees` does for an admin.
- Any other `HrPolicyError` code maps to 409 with that code, so 409 `access_changed` is raised as `HrPolicyError("access_changed")`.

**Switch ids and labels**
- The switch ids are exactly `directory`, `own_requests`, `approves_team`, `hr_team`, `recruitment`, `attendance`, `payroll_prepare` and `payroll_review`.
- The preset ids are `staff`, `manager`, `hr_team`, `payroll_preparer` and `payroll_reviewer`.
- Labels, from the spec:

| Id | Label |
|---|---|
| `directory` | "See everyone's basic details" |
| `own_requests` | "Can request their own training, leave and attendance" |
| `approves_team` | "Approves training, leave and attendance for their team" |
| `hr_team` | "HR team member" |
| `recruitment` | "Works on recruitment" |
| `attendance` | "Works on attendance" |
| `payroll_prepare` | "Prepares payroll" |
| `payroll_review` | "Reviews payroll" |

**Company features**
- The block is titled "What the HR assistant can help with".
- It never greys out or gates a staff switch, and no warning depends on it.
- It saves through the existing `PUT /console/v1/hr/agents`, one call per changed feature.

**Batches**
- At most 50 people per `POST …/access/changes`.
- Ticks apply within the current page, page size 50.

**Repo rules**
- SQL lives in `db/sql/hr/` and `db/migrations/`.
- Synthetic fixtures and disposable test databases only. Never connect to staging or production.
- Copy follows `docs/WRITING_GUIDE.md`.
- Keep existing `data-testid`s. New ones use the prefix `hr-access-`.

**Tooling**
- **Python tests:** run one test file per invocation, from the worktree root:
  `bash /tmp/claude-1000/-home-amsyrzmi-CSAV2/7d09c8ff-ee80-4525-b21a-195503f98d9c/scratchpad/ci.sh /home/amsyrzmi/CSAV2/.venv/bin/pytest -q -p no:cacheprovider <file>`
  The host is memory-constrained. Never run the full suite, because the PR's CI is the merge gate.
- **Python edits:** an editor hook reflows `.py` files to 120 columns. Write `.py` edits through a script or heredoc. Then run `/home/amsyrzmi/CSAV2/.venv/bin/ruff format <file>` and `ruff check <file>`, and check `git diff` for unrelated reflow.
- **Web checks:** run from `CSA_WebConsole/csa-console/web`, with `PATH=/tmp/fourstacks-toolbin:$PATH`, through the ci.sh wrapper: `npm run lint`, `npm run typecheck` and `npm test`. `node_modules` is a symlink to the main checkout's.
- **OpenAPI:** after a route change, run `python -m scripts.export_console_openapi`, then the `tests/test_console_contract_snapshot.py` and `tests/test_hr_contracts.py` files.
- **Lint:** `make lint` before the last commit of each task.

## Review Focus

1. **A console member with no linked employee record.** That member has switches, but the reporting-line warnings and team size must not crash. They show `team_size` 0, and if `approves_team` is on, a "manager_without_team" warning. Pinned in Task 2 by `test_member_without_employee_has_switches_and_no_team`.
2. **The HR owner edits their own access.** Their owner-profile rows (`training.decide`, and the recruitment, attendance and leave `decide` rows, on `client_owner`) count as uncovered. Every save keeps them, so the owner never loses owner decisions from this page. Pinned in Task 3 by `test_owner_saving_own_switches_keeps_owner_decisions`.
3. **More members than one page, and more than 200.** The summary has no cap, and paging with a search returns every match exactly once. Pinned in Task 1 by `test_summary_has_no_member_cap` (205 members), and in Task 3 by `test_people_pages_with_search_return_each_match_once`.
4. **An actor or target who has since been disabled shows up in recent changes.** The event still shows, with the name `None`, which the page renders as "Former member". Pinned in Task 3 by `test_recent_changes_show_disabled_people_without_names`.
5. **One person's change in a batch is refused.** That person gets `refused` with a reason; the others still save. A refusal can come from exceeding `AccessSave`'s 32 grants or 48 workflow rows, or from a policy error. Pinned in Task 3 by `test_batch_reports_a_refusal_per_person_and_saves_the_rest`.

---

### Task 1: Migration 0482: the owner's access summary and reporting lines

**Files:**
- Create: `db/migrations/0482_hr_access_summary.sql`
- Create: `db/sql/hr/access_summary.sql` and `db/sql/hr/access_reporting_lines.sql`, each a `SELECT * FROM public.<function>(%s)`.
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION = "0482"`)
- Test: `tests/test_hr_access_summary.py` (new)

**Interfaces:**
- Produces `public.hr_owner_access_summary(p_client uuid)`, which returns one row per enabled member (`console_memberships.is_enabled` and `console_users.is_enabled`), with these columns:
  - `user_id uuid`
  - `display_name text`
  - `grants jsonb`: an array of `{action, scope, fields}`, from `hr_grants`
  - `workflows jsonb`: an array of `{domain, action, scope}`, from `hr_workflow_grants`
  - `areas jsonb`: an array of area text, from `hr_private_grants`
  - `employee_id uuid`, `department text`, `manager_id uuid` and `active boolean`: the employee linked through `hr_employee_accounts`, or NULL
  - `team_size integer`: active employees whose assigned manager is this member's employee
  - `is_owner boolean`: whether the member is in `hr_client_owners`
- Rows are ordered by `lower(display_name)`, then `user_id`. There is no LIMIT.
- Produces `public.hr_owner_reporting_lines(p_client uuid)`, which returns one row per active employee, with these columns:
  - `employee_id uuid`
  - `display_name text`
  - `manager_employee_id uuid`, which may be NULL
  - `manager_user_id uuid`: the manager's linked, enabled console account, or NULL
- Read the existing `hr_admin_employees` and `hr_admin_members` functions in `0410_hr_operations.sql` for how employee values and account links are stored, and mirror their joins.

- [ ] **Step 1: Write the failing tests** in `tests/test_hr_access_summary.py`.
  - Use the `HrWorld` fixture from `tests/test_hr_persistence.py`, its `world.call` and `world.owner_pool`, the same way `tests/test_hr_operations.py` does.
  - Call the functions through the app pool inside the tenant's session settings. Mirror how the service reads `hr/admin_members.sql`, or add a small helper that runs `q.fetch_all("hr/access_summary.sql", …)` inside `tx` with the owner's HR session.
  - `test_summary_returns_every_members_rows_for_the_owner_only`:
    - The owner sees every enabled member, with their saved `grants`, `workflows` and `areas`.
    - A non-owner admin of the same tenant gets zero rows.
    - The owner of `world.other_tenant` gets zero rows for `world.tenant`.
  - `test_summary_links_employee_team_size_and_owner_flag`:
    - The manager's row has `team_size` ≥ 1, because the staff employee reports to the manager.
    - The owner's row has `is_owner` true.
    - A member with no linked employee has a NULL `employee_id` and `team_size` 0.
  - `test_summary_has_no_member_cap`: insert 205 synthetic members through `world.owner_pool`, and the owner's summary returns all of them.
  - `test_reporting_lines_name_the_managers_console_account`:
    - The staff employee's `manager_user_id` is the manager's user id.
    - An employee whose manager has no linked account has a NULL `manager_user_id`.
  - `test_0482_is_recorded_and_expected`:
    - `'0482'` is in `schema_migrations`.
    - `max(version) == EXPECTED_SCHEMA_VERSION`, using the imported constant.
- [ ] **Step 2: Run the file and see it fail.** Expected: the functions do not exist.
- [ ] **Step 3: Write the migration and the two SQL files, and bump `EXPECTED_SCHEMA_VERSION` to `"0482"`.**
  - The header follows the style of 0480 and 0481: purpose, pre-check and rollback. The rollback is to drop both functions.
  - Use `CREATE OR REPLACE FUNCTION` so the file is safe to run twice.
  - Each function is `LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp`. It starts with `IF NOT public.hr_is_owner(p_client) THEN RETURN; END IF;`.
- [ ] **Step 4: Run the new file, then `tests/test_migrate.py`, `tests/test_health.py` and `tests/test_hr_persistence.py`, one file per run.** Expected: all pass.
- [ ] **Step 5: Commit.** Message: `feat(hr): migration 0482 with the owner's access summary and reporting lines`

### Task 2: The switch rules module

**Files:**
- Create: `csa/hr/access_switches.py`
- Test: `tests/test_hr_access_switches.py` (new; pure, so no database)

**Interfaces:**

**Types:**
- `SwitchId = Literal["directory", "own_requests", "approves_team", "hr_team", "recruitment", "attendance", "payroll_prepare", "payroll_review"]`
- `PresetId = Literal["staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"]`
- `GrantRow(NamedTuple)`: `action: str`, `scope: str`, `fields: tuple[str, ...]`, with `fields` sorted.
- `WorkflowRow(NamedTuple)`: `domain: str`, `action: str`, `scope: str`.
- `AccessRows(NamedTuple)`: `grants: frozenset[GrantRow]`, `workflows: frozenset[WorkflowRow]`, `areas: frozenset[str]`. It has `union`, `minus` and `issubset` helpers, and `AccessRows.from_summary(grants_json, workflows_json, areas_json)`.

**Tables:**
- `SWITCHES: Mapping[SwitchId, AccessRows]`, exactly the spec's table. The five basic fields are `display_name`, `job_title`, `department`, `work_email` and `assigned_manager`.

| Switch | Rows |
|---|---|
| `directory` | `directory.read` on `hr_operator`, with the five fields |
| `own_requests` | `directory.read` on `self` (five fields); `training.read`, `training.draft`, `training.submit` and `training.withdraw` on `self`; attendance and leave `read`/`prepare` on `self` |
| `approves_team` | `directory.read` on `assigned_manager` (five fields); `training.read` and `training.decide` on `assigned_manager`; attendance and leave `read`/`decide` on `assigned_manager` |
| `hr_team` | `directory.read` on `hr_operator` (five fields); `training.read` on `hr_operator`; recruitment `read`/`prepare` on `hr_operator`; attendance and leave `read` on `hr_operator` |
| `recruitment` | area `recruitment` |
| `attendance` | area `attendance` |
| `payroll_prepare` | payroll `read`/`prepare` on `payroll_operator`, and area `payroll` |
| `payroll_review` | payroll `decide` on `payroll_reviewer`, and area `payroll` |

- `PRESETS: Mapping[PresetId, frozenset[SwitchId]]`:
  - `staff` = {`directory`, `own_requests`}
  - `manager` = staff ∪ {`approves_team`}
  - `hr_team` = staff ∪ {`hr_team`, `recruitment`, `attendance`}
  - `payroll_preparer` = staff ∪ {`payroll_prepare`}
  - `payroll_reviewer` = staff ∪ {`payroll_review`}

**Functions:**
- `SwitchState(NamedTuple)`: `on: frozenset[SwitchId]`, `included: Mapping[SwitchId, SwitchId]` (each included switch mapped to the on switch that includes it), and `uncovered: AccessRows`.
- `state(current: AccessRows) -> SwitchState`:
  - a switch is on when `SWITCHES[s].issubset(current)`;
  - `included[a] = b` when `a != b`, `b` is on, and `SWITCHES[a].issubset(SWITCHES[b])`;
  - `uncovered = current.minus(union of the on switches' rows)`.
- `rows(on: frozenset[SwitchId], uncovered: AccessRows) -> AccessRows`: the union.
- `apply_preset(on: frozenset[SwitchId], preset: PresetId) -> frozenset[SwitchId]`: `on | PRESETS[preset]`. It only adds.
- `class AccessLimitError(ValueError)`: raised when the grants exceed 32 or the workflows exceed 48.
- `operations(user_id: UUID, before: AccessRows, after: AccessRows) -> list[AccessSave | PrivateAccessSave]`:
  - one `AccessSave` (from `csa/hr/automation_models.py`) when the grants or workflows differ;
  - one `PrivateAccessSave` per area added (`enabled=True`) or removed (`enabled=False`);
  - `[]` when they are equal;
  - it raises `AccessLimitError` before building an over-limit `AccessSave`.
- `grant_switches(grants: frozenset[GrantRow], workflows: frozenset[WorkflowRow]) -> frozenset[SwitchId]`: the switches that have grant or workflow rows, and whose grant and workflow rows are all present. Recent changes use it to compare `access.save` bodies, which carry no areas.
- `PersonAccess(NamedTuple)`: `user_id`, `display_name`, `rows: AccessRows`, `employee_id`, `department`, `manager_id`, `active`, `team_size: int`, `is_owner: bool`, and the classmethod `from_summary(row)`.
- `ReportingLine(NamedTuple)`: `employee_id`, `display_name`, `manager_employee_id`, `manager_user_id`.
- `AccessWarning(NamedTuple)`:
  - `kind: Literal["no_approver", "no_manager", "no_payroll_reviewer", "manager_without_team", "prepares_and_reviews"]`
  - `user_id: UUID | None`
  - `employee_id: UUID | None`
- `warnings(people: Sequence[PersonAccess], lines: Sequence[ReportingLine]) -> list[AccessWarning]`:
  - `no_approver`: an employee whose `manager_employee_id` is set, but whose `manager_user_id` is NULL or lacks `approves_team`;
  - `no_manager`: an employee with no manager;
  - `no_payroll_reviewer`: nobody has `payroll_review`;
  - `manager_without_team`: `approves_team` is on and `team_size == 0`;
  - `prepares_and_reviews`: both payroll switches are on for one person.
  - The result is sorted deterministically, by kind and then by id.
- `stamp(people: Sequence[PersonAccess], lines: Sequence[ReportingLine]) -> str`: a SHA-256 hex digest over a canonical JSON of every person's `user_id` and sorted rows, plus every reporting line. It does not depend on order.

**Steps**

- [ ] **Step 1: Write the failing tests** in `tests/test_hr_access_switches.py`:
  - `test_every_switch_set_is_accepted_by_the_operation_models`: for each switch, `operations(uuid4(), EMPTY, SWITCHES[s])` builds models without errors. This covers the `ResolvedGrant` and `WorkflowGrant` validators.
  - `test_state_round_trips_each_switch_and_preset`: for each switch and each preset `p`, `state(rows(PRESETS[p], EMPTY)).on == PRESETS[p]`, apart from switches that are included.
  - `test_hr_team_includes_directory`: `state(SWITCHES["hr_team"])` has `included == {"directory": "hr_team"}`, and `directory` is not counted as separately removable.
  - `test_shared_payroll_area_stays_until_both_payroll_switches_are_off`: removing `payroll_review` while `payroll_prepare` stays on keeps area `payroll`; removing both drops it.
  - `test_partial_and_owner_rows_are_uncovered_and_survive`:
    - a `directory.read`/`self` grant with only `display_name` is uncovered;
    - the owner-profile rows (`training.decide`/`client_owner`, and recruitment, attendance and leave `decide`/`client_owner`) are uncovered;
    - `rows(frozenset(), uncovered)` keeps them all.
  - `test_presets_only_add`: `apply_preset({"payroll_review"}, "hr_team")` still contains `payroll_review`.
  - `test_operations_split_grants_and_private_areas`:
    - turning on `recruitment` alone gives exactly one `PrivateAccessSave(area="recruitment", enabled=True)` and no `AccessSave`;
    - turning on `approves_team` gives exactly one `AccessSave`.
  - `test_over_limit_rows_raise_access_limit_error`: 33 synthetic uncovered grants raise `AccessLimitError`.
  - `test_warnings_each_kind`: build synthetic people and reporting lines that trigger each of the five kinds exactly once.
  - `test_member_without_employee_has_switches_and_no_team` (Review Focus 1): a person with `employee_id=None`, `team_size=0` and `approves_team` on gives one `manager_without_team` warning and no error.
  - `test_stamp_ignores_order_and_changes_with_any_row`: shuffled input gives the same stamp; adding one grant row or changing one manager gives a different one.
  - `test_grant_switches_ignores_areas`: `payroll_prepare`'s workflow rows alone give {`payroll_prepare`}; `recruitment` never appears.
- [ ] **Step 2: Run the file and see it fail** (module missing).
- [ ] **Step 3: Implement `csa/hr/access_switches.py`.** No database access and no I/O.
- [ ] **Step 4: Run the file and see it pass.** Run `make lint`.
- [ ] **Step 5: Commit.** Message: `feat(hr): the switch rules for the HR access page`

### Task 3: Service, routes, recent changes and the API contract

**Files:**
- Create: `csa/hr/access_contracts.py`, `csa/hr/access_page.py`
- Create: `db/sql/hr/access_event_page.sql`, `db/sql/hr/access_event_previous.sql`
- Modify: `csa/hr/api.py` (three routes in the existing router)
- Modify: `docs/console_openapi.json` (regenerated), `docs/CONSOLE_API.md` (an "HR access page" subsection under the HR section)
- Test: `tests/test_hr_access_page.py` (new)

**Interfaces:**
- Consumes everything from Task 2, plus `hr/access_summary.sql` and `hr/access_reporting_lines.sql` from Task 1.

**Contracts** (`csa/hr/access_contracts.py`, Pydantic, following `HrModel` conventions):
- `AccessPerson`:
  - `user_id`, `display_name: str | None`, `employee_id`, `department`, `manager_id`, `team_size: int`, `is_owner: bool`
  - `on: list[SwitchId]`, `included: dict[SwitchId, SwitchId]`
  - `custom: list[str]`: the uncovered rows in plain words, for example "Records client-owner decisions on leave"
- `AccessPeoplePage`: `items: list[AccessPerson]`, `next_cursor: str | None`, `version: str`, `counts: dict[SwitchId, int]` (whole client), `warnings: list[AccessWarningOut]`.
- `AccessWarningOut`: `kind`, `user_id`, `employee_id`, `user_name`, `employee_name`, `manager_name`. The names are resolved by the service; the page writes the sentence.
- `AccessChange`: `user_id: UUID`, `on: list[SwitchId]` (max 8, unique). This is the person's full desired switch set.
- `AccessChanges`: `batch_id: UUID`, `version: str`, `dry_run: bool = False`, `changes: list[AccessChange]` (1–50, unique `user_id`).
- `AccessPersonResult`: `user_id`, `outcome: Literal["saved", "unchanged", "refused"]`, `reason: str | None`.
- `AccessChangeResult`: `results: list[AccessPersonResult]`, `version: str`, `warnings: list[AccessWarningOut]`.
- `AccessEvent`: `occurred_at`, `actor_user_id`, `actor_name: str | None`, `target_user_id`, `target_name: str | None`, `turned_on: list[SwitchId]`, `turned_off: list[SwitchId]`, `area: str | None`, `area_enabled: bool | None`.
- `AccessEventPage`: `items`, `next_cursor`.

**Service** (`csa/hr/access_page.py`): `class HrAccessPage`, built as `HrAccessPage(core: HrService)`.
- `people(session, *, cursor: str | None, q: str | None, team: str | None, has: SwitchId | None, reports_to: UUID | None) -> AccessPeoplePage`:
  - Inside `tx(core.pool, tenant)`: run `core._access` and `HrOperations.owner(access)` (the 404 on denial comes from the existing error mapping), read the summary and the reporting lines, then `core._session`.
  - Filter in Python:
    - `q`: case-insensitive substring of `display_name`;
    - `team`: equals `department`;
    - `has`: the switch is on or included;
    - `reports_to`: equals `manager_id`.
  - The page size is 50. The cursor is an opaque offset over the filtered, ordered list.
  - `counts`, `warnings` and `version` are always computed over the whole client.
- `changes(session, body: AccessChanges) -> AccessChangeResult`:
  1. Run the owner check, then load the summary and the reporting lines.
  2. Derive keys: for `access.save` use `uuid5(body.batch_id, f"{user_id}:access")`; for each area use `uuid5(body.batch_id, f"{user_id}:area:{area}")`.
  3. **Replay:** if every derived key already has a row (`hr/op_get.sql` for the owner's `subject_id`), skip the stamp check, call `HrOperations.apply` with the same keys (it returns `duplicate`), and report `saved`.
  4. Otherwise, if `stamp(...) != body.version`, raise `HrPolicyError("access_changed")`. `hr_http_error` turns it into a 409.
  5. For each change, work out the desired rows: `after = rows(desired_on, state(before).uncovered)`. An unknown `user_id` gives `refused`, reason `not_a_member`.
  6. `ops = operations(...)`. Empty `ops` gives `unchanged`. `AccessLimitError` gives `refused`, reason `too_many_rows`.
  7. If `dry_run`, apply nothing, report what would happen, and return the warnings for the proposed state.
  8. Otherwise apply each op with `HrOperations(core).apply(session, op, key)`. `HrPolicyError` gives `refused` with its code; the other people continue.
  9. Return the reloaded `version` and the `warnings`.
- `recent(session, *, cursor: str | None) -> AccessEventPage`:
  - Run the owner check.
  - `access_event_page.sql` selects `hr_authority_events` rows with `operation IN ('access.save','private_access.save')` for the tenant. The cursor is `(occurred_at, request_key)`, rows come newest first, the page holds 50, and it reads through the existing owner RLS policy.
  - For each `access.save`, the previous `access.save` for the same `details->>'user_id'` comes from the page, or else from `access_event_previous.sql`.
  - `turned_on` and `turned_off` are the differences between `grant_switches` of the two. With no previous event, compare against empty.
  - Names come from the summary. A user who is no longer enabled gets `None`.

**Routes** (`csa/hr/api.py`, existing `router`, `Session` and `Service` dependencies):
- **Body limit:** `HrRoute` caps request bodies at 8192 bytes for every path except `/console/v1/hr/operations`. Allow 65536 for `/console/v1/hr/access/changes`, since 50 people fit well under that. Keep the 8192 limit for everything else.
- `GET /access/people`, with query `cursor`, `q`, `team`, `has` and `reports_to` → `AccessPeoplePage`
- `POST /access/changes`, with body `AccessChanges` → `AccessChangeResult`
- `GET /access/recent`, with query `cursor` → `AccessEventPage`

**Steps**

- [ ] **Step 1: Write the failing tests** in `tests/test_hr_access_page.py`, using `HrWorld` and `world.call`. A local helper `people(world, who, **params)` returns the JSON.
  - `test_people_page_is_owner_only`: the owner gets 200; `admin` and `staff` get 404 `resource_unavailable`; the other tenant's owner gets 404.
  - `test_fifty_person_batch_fits_the_body_limit`: a 50-person `dry_run` batch with all eight switch ids each returns 200, not 413.
  - **One test per switch,** parametrised over the eight ids: `test_switch_on_then_off_writes_exact_rows[<id>]`.
    - The owner sends `changes` turning only that switch on for `operator`.
    - The grant, workflow and area rows read through `world.owner_pool` equal `SWITCHES[id]`, plus whatever was there before.
    - Turning it off restores the starting rows.
  - `test_uncovered_rows_survive_every_save`: seed a partial grant for `operator` through the old `access.save` operation. Turn `approves_team` on, then off. The partial grant is still present, and the person's `custom` is non-empty.
  - `test_owner_saving_own_switches_keeps_owner_decisions` (Review Focus 2): seed the owner profile rows on the owner. The owner turns their own `own_requests` on and off. The `client_owner` decide rows remain.
  - `test_stale_version_is_409_and_writes_nothing`: read the version, save one change through the old operation route, then post `changes` with the old version. The result is 409 `access_changed`, and the rows are unchanged.
  - `test_replayed_batch_returns_saved_without_reapplying`: post the same body twice. The second returns the same outcomes, and `hr_operations` has no extra rows.
  - `test_unchanged_people_write_nothing`: post the current switches. The outcome is `unchanged`, with no new `hr_operations` rows.
  - `test_batch_reports_a_refusal_per_person_and_saves_the_rest` (Review Focus 5): in one batch, one change for a non-member gives `refused`/`not_a_member`, and one valid change gives `saved`.
  - `test_dry_run_returns_warnings_and_writes_nothing`:
    - turning `approves_team` off for the manager gives a `no_approver` warning for the staff employee;
    - no rows change.
  - `test_people_pages_with_search_return_each_match_once` (Review Focus 3): seed 120 members named "Synthetic NNN" through `world.owner_pool`. Paging with `q=synthetic` returns 120 unique `user_id`s across three pages.
  - `test_counts_and_has_filter_cover_the_whole_client`: after turning `approves_team` on for two people, `counts["approves_team"] == 2`, and `has=approves_team` returns those two.
  - `test_recent_changes_show_what_changed`: turn `approves_team` on for `operator`, then `payroll_prepare` on. The newest event has `turned_on == ["payroll_prepare"]`; the one before has `["approves_team"]`. A private-area event reports `area` and `area_enabled`.
  - `test_recent_changes_show_disabled_people_without_names` (Review Focus 4): disable `operator` through `world.owner_pool` after a change. Their event remains, with `target_name` set to `None`.
- [ ] **Step 2: Run the file and see it fail** (routes missing).
- [ ] **Step 3: Implement the contracts, the two SQL files, the service and the routes.**
- [ ] **Step 4: Regenerate `docs/console_openapi.json`. Run `tests/test_hr_access_page.py`, `tests/test_console_contract_snapshot.py`, `tests/test_hr_contracts.py` and `tests/test_hr_operations.py`, one file per run.** Expected: all pass.
- [ ] **Step 5: Document the three routes in `docs/CONSOLE_API.md`'s HR section.**
  - Cover: owner-only, page size 50, the 409 `access_changed`, replay by `batch_id`, `dry_run`, and per-person outcomes.
  - State that the company features control only the HR assistant.
  - Run `make lint`.
- [ ] **Step 6: Commit.** Message: `feat(hr): access page service, routes and recent changes`

### Task 4: The "Who can do what" page (layout B)

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/hr-access-api.ts`: the types mirroring Task 3's contracts, and the calls.
- Create: `CSA_WebConsole/csa-console/web/lib/hr-access-copy.ts`: the labels, explanations, groups, preset names, warning sentences and custom-row wording.
- Create: `CSA_WebConsole/csa-console/web/components/console/HrAccessPage.tsx`
- Modify: `CSA_WebConsole/csa-console/web/components/console/HrAutomation.tsx`. The owner's "People & access" section renders `<HrAccessPage tenant=… readonly=… />` first. The existing Employee, Access profile, Background maintenance and Private access forms move inside a `<details>` titled "Advanced", unchanged.
- Test: `CSA_WebConsole/csa-console/web/tests/hr-access-api.test.mjs` (new), in the style of `tests/subscriptions-api.test.mjs`.

**Interfaces:**
- Consumes Task 3's routes and contracts.
- Produces these exports from `lib/hr-access-api.ts`:
  - `accessPeople(tenant: string, params: {cursor?, q?, team?, has?, reports_to?}) → Promise<AccessPeoplePage>`
  - `saveAccessChanges(tenant: string, body: AccessChanges) → Promise<AccessChangeResult>`
  - `accessRecent(tenant: string, cursor?: string) → Promise<AccessEventPage>`
  - All three use `/console/v1/hr/access/...` with the same `?tenant=` suffix that `hrClient` in `lib/hr-api.ts` uses.
- Produces these exports from `lib/hr-access-copy.ts`:
  - `SWITCH_COPY: Record<SwitchId, {label, explanation, group: "Everyday" | "Responsibilities" | "Private areas"}>`, with the labels from Global Constraints;
  - `PRESET_COPY`;
  - `warningText(w: AccessWarningOut): string`;
  - `formerMember(name: string | null): string`, which returns "Former member" for `null`.

**Page behaviour (spec, "Page (layout B)"):**
- **The left column** has:
  - a search box and the filters Team, Reports to, and Has switch (with counts);
  - the list rows: initials, name, short tags for the on switches, a "Custom access" marker, and a warning marker;
  - a tick box on each row, and paging.
- **The right column** is the selected person's panel:
  - their switches, grouped, with explanations;
  - included switches show "Included with <label>" and are disabled;
  - a Preset menu and a "Copy access from…" menu;
  - their custom-access rows, with a link that opens "Advanced";
  - their warnings.
- **Bulk change:** with one or more ticks, a bar offers "Apply preset" and "Turn on/off <switch>" for the ticked people.
- **Save bar:** pending changes are kept per person, as the full desired set. The bar shows "N changes for M people", Discard and Save.
  - Save first posts with `dry_run: true` and shows the warnings in a confirm dialog. Confirming posts for real, with the same `batch_id`.
  - Per-person results show inline. Refused people stay pending, with the reason.
  - On 409, it shows: "Someone changed access since you opened this page. Reload to see the latest; your pending changes are kept." Reload refetches and keeps the pending changes.
  - A new `batch_id` (`crypto.randomUUID()`) is made when the pending set changes, not on retry.
- **Company features block:** titled "What the HR assistant can help with", with an explanation that it doesn't change what staff can do. It uses the existing agent switch read and `PUT /console/v1/hr/agents`.
- **Recent changes:** a collapsible panel using `accessRecent`, with "Load more".
- **Phone width:** the list and the panel become two screens, with a back button. The save bar stays at the bottom.
- **Test ids:**
  - `hr-access-search`, `hr-access-row-<user_id>`, `hr-access-panel`, `hr-access-switch-<id>`, `hr-access-save`, `hr-access-confirm`, `hr-access-result-<user_id>`, `hr-access-recent`;
  - `hr-access-tick-<user_id>`, `hr-access-bulk`, `hr-access-preset`, `hr-access-copy-from`, `hr-access-conflict`.

**Steps**

- [ ] **Step 1: Write the failing node tests** in `tests/hr-access-api.test.mjs`:
  - `accessPeople` builds `/console/v1/hr/access/people?tenant=…&q=…&has=…` and omits empty params;
  - `saveAccessChanges` posts JSON with `batch_id`, `version`, `dry_run` and `changes`, and sends the CSRF header like the other posts;
  - a 409 surfaces as an `ApiError` with status 409;
  - `warningText` gives a sentence that names the employee and the manager for `no_approver`, and "Only you, as HR owner, can approve these" for `no_manager`;
  - `formerMember(null)` is "Former member".
- [ ] **Step 2: Run `npm test` and see it fail.**
- [ ] **Step 3: Implement the two lib modules, the component and the `HrAutomation.tsx` change.** Use the existing console components (`components/console/ui.tsx`, `components/ui/*`) and today's styling. The restyle comes later, from the redesign session.
- [ ] **Step 4: Run `npm run lint`, `npm run typecheck` and `npm test`.** Expected: clean and passing.
- [ ] **Step 5: Commit.** Message: `feat(console): the "Who can do what" HR access page`

### Task 5: Browser acceptance test and progress record

**Files:**
- Create: `tests/test_hr_access_browser.py`, modelled on `tests/test_subscriptions_browser.py`. Reuse the helpers it imports from `tests/test_services_browser.py`.
- Modify: `docs/PROGRESS.md`. Add two entries at the top:
  - **Lane B entry for B2.** Cover what was built, migration 0482, the owner decisions of 2026-10-28 (layout B, server-side rules, company features label only the assistant, the directory switch shows everyone's basic details), and the focused test counts from the earlier tasks.
  - **Renewal-anchor staging rollout record** (2026-10-28):
    - main `68c14fc`, PR #43;
    - CI run 40661554317 and publish run 40665022478;
    - `csa-python@sha256:d1c93cf1b5bceeefd5213dd7a96964370272539407d33d74d47c1102a9081465` and `csa-frontend@sha256:3cd64b51bbbe4844c543cb88538721dc0e2973cbda8dd9766ebd03e4363f6d9b`;
    - 0481 applied through the `migrate` one-shot, with 2 of 2 subscriptions anchored;
    - the drift check returned 0 rows, run before any recreate;
    - gateway, worker, delivery and frontend recreated and healthy;
    - `/readyz` ok, and `/console/v1/subscriptions/plans` returned 401.

**Interfaces:**
- Consumes Task 4's `data-testid`s.

**Steps**

- [ ] **Step 1: Write `test_hr_access_owner_changes_two_people_and_staff_sees_it`.**
  1. Seed a tenant with an HR owner, a manager and a staff member, with synthetic data and linked employees.
  2. The owner opens People & HR → People & access.
  3. They search for the staff member and turn on "See everyone's basic details" for them.
  4. They tick the manager and the staff member, and apply the "Manager" preset to the manager only.
  5. They save. The dry-run dialog shows, they confirm, and both rows show "Saved".
  6. A second browser context logs in as the staff member and sees a colleague's job title in the HR directory, which they could not see before.
  7. A stale tab (a second owner page opened before the save) shows the `hr-access-conflict` message when it saves.
- [ ] **Step 2: Run it once locally**, if the browser stack starts on this host: `ci.sh … pytest -q -p no:cacheprovider tests/test_hr_access_browser.py`, with `node_modules` symlinked. If the host cannot run it, say so in the report. The PR's CI runs it.
- [ ] **Step 3: Update `docs/PROGRESS.md` as listed above, and run `make lint`.**
- [ ] **Step 4: Commit.** Message: `test(hr): browser acceptance for the HR access page; record B2 and the 0481 rollout`
