# HR access page: "Who can do what"

Lane B, item B2 of the client growth program
(`docs/superpowers/specs/2026-10-27-client-growth-program-design.md`, section B2). Date: 2026-10-28.
This design replaces the program design's B2 section where they differ; the differences are listed
under "Changes from the program design".

## Intent

A client's HR owner today grants HR access one member at a time through the "Access profile" and
"Private access" forms in People & HR → "People & access" (`components/console/HrAutomation.tsx`).
They choose raw profiles ("Assigned manager", "Payroll preparer") and private areas. The forms do not
show what anyone currently has, so the owner cannot answer "who can approve leave?" without
remembering, and a save replaces the whole set without warning about what it drops.

The owner wants a plain page where they see each person's HR access as switches with plain
explanations, change one or many people safely, and are warned before a change leaves work with
nobody to do it. The current client has 15 staff; the page must work for any number.

## Goals

- One page, "Who can do what", under People & HR, for the HR client owner only.
- Each console member's access shown as plain switches, with an explanation under each.
- Change one person, or tick several and apply a preset or a switch to all of them.
- Nothing is removed silently: access the switches do not describe is kept and marked.
- Warnings before a save that would leave nobody to approve or review, and for switches that do
  nothing.
- A save never overwrites a change made since the page loaded.
- A recent-changes view of who changed whose access, and when.
- Works for any number of members, on desktop and phone.

## Non-goals

- No new console roles (owner rule: roles are not HR authority). Presets only fill in switches.
- No new tables. No change to how HR permissions are enforced.
- Default access for new invitees: a separate small follow-on right after B2 (owner, 2026-10-28).
- The client-owner assignment itself is not a switch; it stays under "Advanced".
- Restyling to the FourStacks UI guide: the console redesign session does that after B2 merges.
  B2 uses today's console components.

## Anti-goals

- A save that removes access the owner could not see.
- A page that looks like it changed access when the server refused.
- Two tabs silently undoing each other.

## Owner decisions (2026-10-28)

- **Layout B: people list with a detail panel.** A searchable, filterable list shows each person
  with short tags for their access. Selecting a person shows all their switches, grouped
  ("Everyday", "Responsibilities", "Private areas"), each with an explanation. Ticking several people
  shows a bar to apply a preset or a single switch to all of them. On phones the list and the
  panel are separate screens.
- **Approach B: the server works out the switches.** A Python module owns the mapping from switches
  to grant rows, the presets and the warnings. The page shows what the server returns and sends
  changes back. (The program design put the mapping in a TypeScript module; reversed so the warnings
  and the stale-tab check are correct and the rules sit next to the code that enforces them.)
- **First version includes all of these:**
  - search, filters ("Team", "Has switch"), a count per switch in the filter;
  - bulk change of selected people; presets (Staff, Manager, HR team, Payroll preparer, Payroll
    reviewer); "Copy access from…";
  - a save bar with pending changes, Save and Discard; results per person;
  - warnings: an employee whose manager cannot approve them, nobody reviewing payroll, a manager
    with no team, the same person preparing and reviewing payroll (warn, not block);
  - a "Custom access" marker on people whose access the switches do not fully describe;
  - the stale-tab check;
  - a recent-changes panel;
  - paging past roughly 100 people.
- **Company features block** on the same page, titled "What the HR assistant can help with":
  Directory, Training, Recruitment, Attendance and leave, Payroll, HR assistant, saved through the
  existing `HrService.configure_agent` path. **Owner decision (option a):** these switches keep
  today's meaning. They control only the HR assistant and agents (`hr_agent_settings` is checked only
  when a session has a `capability`, `csa/hr/service.py` `_require_capability`); they never limit what
  staff can do in the console. The page says so, and no warning or greyed state depends on them.
- **"See everyone's basic details"** (owner decision): the directory switch gives `directory.read`
  on scope `hr_operator` limited to the five basic fields, so the person can look up any colleague's
  name, job title, department, work email and manager. A company phone book.

## Approaches considered

- **A. The browser works out the switches** (the program design's plan): the read function returns
  raw grant rows; a TypeScript module derives switches and builds `access.save` bodies; the page
  sends one save per person. Least server code, but warnings are computed from data that may be
  stale, the stale-tab check cannot be made reliable because the server never sees the version the
  page showed, and the mapping lives away from the enforcement code. Rejected.
- **B. The server works out the switches** (chosen): described below.
- **New console roles:** rejected by the program design and the owner.

## Design

### Switches and their grant sets

Each switch maps to one fixed set of rows. The sets reuse today's profiles from
`HrAutomation.tsx` so existing grants read back as the same switches.

| Switch (label) | Rows when on |
|---|---|
| See everyone's basic details (name, job title, team, work email, manager) | `hr_grants` `directory.read` on `hr_operator`, fields display_name, job_title, department, work_email, assigned_manager |
| Can request their own training, leave and attendance | the "self" profile: `hr_grants` `directory.read`/`training.read`/`training.draft`/`training.submit`/`training.withdraw` on `self`; `hr_workflow_grants` attendance and leave `read`/`prepare` on `self` |
| Approves training, leave and attendance for their team | the "manager" profile: `directory.read`, `training.read`, `training.decide` on `assigned_manager`; attendance and leave `read`/`decide` on `assigned_manager` |
| HR team member (reads HR records, prepares recruitment) | the "hr" profile: `directory.read`, `training.read` on `hr_operator`; recruitment `read`/`prepare` and attendance, leave `read` on `hr_operator` |
| Works on recruitment | `hr_private_grants` area `recruitment` |
| Works on attendance | `hr_private_grants` area `attendance` |
| Prepares payroll | payroll `read`/`prepare` on `payroll_operator`, plus `hr_private_grants` area `payroll` |
| Reviews payroll | payroll `decide` on `payroll_reviewer`, plus `hr_private_grants` area `payroll`; the area row is removed only when both payroll switches are off |

- **State:** a switch is on when every row of its set is present.
- **Overlapping switches (my call).** Some sets share rows: "HR team member" contains the directory
  row on `hr_operator`, so it includes "See everyone's basic details". A switch whose whole set is
  contained in another switch that is on shows "Included with <switch>" and cannot be turned off on
  its own; turning the other switch off frees it and keeps its own value. `rows()` builds the union
  of all on switches plus uncovered rows, so a shared row stays while any switch needs it. The two
  payroll switches share the `payroll` area row the same way.
- **Custom access:** any row of a person's grants that no fully-on switch covers is "uncovered"
  (for example the owner profile, a partial profile, or rows from the old form). Uncovered rows are
  always re-sent with every save, so a save never removes them. A person with uncovered rows gets
  the "Custom access" marker, and their panel lists those rows in plain words with a link to
  "Advanced", where the old forms stay.
- **Presets** are fixed switch combinations, defined in the same module: Staff (directory, own
  requests); Manager (Staff + approves team); HR team (Staff + HR team member + works on recruitment
  + works on attendance); Payroll preparer (Staff + prepares payroll); Payroll reviewer (Staff +
  reviews payroll). **Presets only turn switches on (my call):** applying "HR team" to a payroll
  reviewer keeps their payroll review. Switches are turned off by hand. Uncovered rows stay.
- **Copy access from…** sets the target's switches to the source's switches. Uncovered rows are not
  copied.

### Read: `public.hr_owner_access_summary(p_client)` (migration 0482)

- `SECURITY DEFINER`, `SET search_path=pg_catalog,public,pg_temp`, returns nothing unless
  `public.hr_is_owner(p_client)`, the same guard as `hr_admin_members`.
- Returns one row per enabled member of the client: `user_id`, `display_name`, the member's
  `hr_grants`, `hr_workflow_grants` and `hr_private_grants` rows (as JSON arrays), the linked
  employee (`employee_id`, `department`, `manager_id`, `active`) if any, `team_size` (active
  employees whose assigned manager is that member's employee), and whether the member is an HR
  client owner.
- A second set-returning part (or a sibling function in the same migration) lists active employees
  with their `manager_id` and whether that manager has a linked, enabled console account, so the
  per-employee approver warning can be computed. Employees without a manager are listed too.
- Unlike `hr_admin_members` it has no 200-member cap; the service pages it.
- Grants: `EXECUTE` to `csa_app` only, revoked from `PUBLIC`, `csa_ro`, `csa_identity`. No RLS
  policy changes. `EXPECTED_SCHEMA_VERSION` becomes `0482`.

### Server module and routes

- `csa/hr/access_switches.py` (new): the switch table, presets, `state(rows) → switches + uncovered`,
  `rows(switches, uncovered) → AccessSave + private area changes`, and `warnings(all members,
  proposed changes, company features)`. Pure functions, unit-tested.
- **Version stamp (my call):** a hash over the whole client's summary: every member's grant rows and
  every active employee's manager link. Any access or reporting-line change since the page loaded
  changes it. Company features are not in it (they do not affect staff access). Returned by the read;
  required on save.
- **Not atomic across a batch (my call, accepted):** the stamp is checked once, then each operation
  (a person's `access.save`, then each private-area change) runs in its own transaction through
  `HrOperations.apply`, which takes the tenant lock itself. Two owners saving within the same moment
  can both pass the check; each operation is still atomic and audited, but one person's save can
  stop part-way (reported `partly_saved`). Each change also carries the `base` switches the page
  loaded, and a person whose switches moved since is refused `changed_since_load`. Documented
  rather than adding a new locked code path.
- **Routes** (in the existing `/console/v1/hr` router in `csa/hr/api.py`, with the same session,
  tenant and owner checks as the owner-only `records/{area}` reads such as `members`):
  - `GET /console/v1/hr/access/people?cursor=&q=&team=&has=`: a page of people with switches, tags, uncovered
    rows in plain words, `team_size`, warnings, the version stamp, and the per-switch counts for the
    whole client.
  - `POST /console/v1/hr/access/changes`: `{batch_id, version, dry_run, changes: [{user_id,
    switches}]}`, at most 50 people per call. Each person's idempotency key is derived from
    `batch_id` and `user_id` (UUIDv5). **Replays first (my call):** if any person's key already has
    a receipt, the batch is a retry and the stamp is not checked, so a retry after a lost response is
    safe. Each person with an `access.save` receipt is compared with its fingerprint (the same rows
    finish any missing area changes; different rows are refused `access_changed`), and everyone else
    is still checked against their `base`. People whose computed rows equal their current rows are skipped as `unchanged`
    and write nothing. For the rest, the server reloads the summary, recomputes the stamp, and
    refuses the whole batch with 409 `access_changed` if it differs. Otherwise it applies each person through the existing
    `HrOperations` path (`access.save`, then `private_access.save` per changed area), each with its
    own idempotency key, so each person gets its own `hr_operations` receipt and `hr_authority_events`
    row as today. The response lists a result per person (`saved`, `unchanged`, `refused` with a
    reason) and the new version.
  - Warnings are returned by the read for the current state and by a dry-run flag on the save
    (`dry_run: true`) for the proposed state. The page shows them before the owner confirms.
- **Recent changes (my call):** a new owner-only, paged read (new SQL file over
  `hr_authority_events`, filtered to `access.save` and `private_access.save`, newest first, cursor
  on `occurred_at`). Each `access.save` event holds the person's full new set, so the service turns
  it into switches with `state()` and compares with that person's previous event to show what
  changed ("You turned on Approves for Daniel Lim"). Names are resolved in the same read, including
  members since disabled ("Former member" when no name is available). No new tables.
- **Company features** use the existing `PUT /console/v1/hr/agents` route, one call per changed
  feature, outside the access batch, as today (no receipt).
- Contract: `CONSOLE_API.md`, `console_openapi.json` and the contract snapshot gain the two routes.

### Warnings

- **Employee nobody can approve (my call, per employee):** after the change, an active employee's
  assigned manager has no linked console account, or that account lacks "Approves … for their team"
  ("Aina Rahman's manager, Daniel Lim, can't approve her leave"). An employee with no assigned
  manager is listed once as "No manager set". HR client owners can decide as a fallback, so the
  warning says "Only you, as HR owner, can approve these". Shown on the read and the dry run; the
  owner may continue.
- **Nobody reviews payroll:** after the change, nobody has "Reviews payroll".
- **Manager with no team:** the approves switch is on and `team_size` is 0.
- **Prepares and reviews payroll:** both on for the same person. Warn, not block.

### Page (layout B)

- Route: People & HR → "Who can do what", the first item of the owner's "People & access" section.
  The old forms move under an "Advanced" disclosure on the same section.
- Left: search, filters ("Team" = department, "Reports to", "Has switch" with counts), the list (avatar initials, name, tags,
  "Custom access" and warning markers), paging, tick boxes. Right: the selected person's panel with
  grouped switches, explanations, preset and "Copy access from…" menus, custom-access rows and
  warnings.
- A save bar appears with pending changes ("3 changes for 2 people"), Discard and Save. Save first
  runs the dry run and shows warnings; the owner confirms. Results show per person; failed rows stay
  pending with the reason.
- On 409 `access_changed`: "Someone changed access since you opened this page. Reload to see the
  latest; your pending changes are kept." Reload keeps pending changes that still apply.
- Phone: list screen, then the person screen; the save bar stays at the bottom.
- Copy follows `docs/WRITING_GUIDE.md`. `data-testid`s are added for the Playwright flow.

## Constraints

- Migration `0482` in lane B's range; SQL in `db/sql/hr/`; never FORCE RLS; no new tables or roles.
- Every write goes through the existing `HrOperations` authorization, receipts and authority events.
- Synthetic fixtures and disposable test databases only.
- Local checks: the changed files' tests one file at a time, the web app's checks, and `make lint`;
  the PR's CI is the merge gate.
- Staging rollout: publish, `migrate` one-shot for 0482, recreate gateway, worker, delivery and
  frontend.

## Changes from the program design

- The directory switch uses scope `hr_operator` (everyone's five basic fields), not `self`, which
  shows only the person's own record (owner decision 2026-10-28).
- Company features are labelled as the HR assistant's and never limit staff (owner decision).
- The mapping, presets and warnings live in Python, not a TypeScript module (approach B).
- Layout B instead of an unspecified layout; scope widened to the owner's 2026-10-28 list.
- The summary also returns the linked employee and team size, and has no member cap.
- Saves go through a batch route with a version stamp instead of the page calling
  `hr_admin_apply` per person directly.

## Left to the builder

- Exact SQL of the summary and the hash used for the version stamp.
- Explanation text under each switch, following the writing guide.
- Page size, empty states and the exact phone breakpoints.
- How a selection is limited: ticks apply within the current page of the list (page size 50).
- `rows()` fails clearly if a person's switches plus uncovered rows exceed `AccessSave`'s limits
  (32 grants, 48 workflow rows).
- Test names. Tests cover: each switch's rows after on and after off; overlapping switches;
  presets add only; copy; uncovered rows survive every save; each warning; replay of a batch;
  unchanged people write nothing; the 409 on a stale version; per-person results with one
  refusal in a batch; the summary function's owner guard and cross-tenant denial; the contract
  snapshot; a Playwright flow (owner changes two people, a staff member sees the change).
