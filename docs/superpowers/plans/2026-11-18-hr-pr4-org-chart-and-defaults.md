# HR PR 4: org chart and Malaysian default templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the People-tab org chart into a real chart (collapsible, department colour, starting and leaving markers, filters, search, print, CSV) and add an English-only "create the Malaysian default templates" action that stays switched off until a named reviewer has signed the content.

**Architecture:** The org chart is additive: the existing `GET /console/v1/hr/org-chart` gains optional node fields (no migration, the rows are already loaded) and everything else (filters, search, expand, CSV) runs in the browser on the full chart. The defaults are data: one content file with a source and a checked date per step, a generated review document, and one run-once route that composes the existing `hr_template_apply`, refused by a deploy switch until the review is signed.

**Tech Stack:** Python/FastAPI/psycopg (csa/hr), Postgres definer functions (only if the atomic variant is chosen in Task 5), Next.js/React/TypeScript (CSA_WebConsole/csa-console/web), node `.mjs` unit tests, Playwright browser tests, the shared `tests/hr_process_world.py` and `tests/test_hr_access_browser.py` stubs.

**Spec:** `docs/superpowers/specs/2026-11-16-hr-processes-design.md` (sections 5.5 org chart, 5.7 rulings 8 and 10, 6 Malaysian grade, 7 default content). Owner decisions after the spec, which override it: **English only** (no Malay text anywhere in the defaults), "follow Malaysian law and standard HR practice", the review gate stays (official sources, named reviewer), the Malay assistant work is a separate PR (see "Not in this PR").

## Global Constraints

- Repo is PUBLIC: synthetic names only in tests and fixtures; official URLs and form names are fine; no real company data.
- No rate, ceiling, threshold, percentage or amount in code, content or notes. `reference_note` names the official body, form or portal, the timing wording, the URL and the date checked, and carries no money figures (`%`, `RM`, digits followed by a currency word).
- A statutory step is a reminder with a reference, never a filing or a calculation.
- Colour literals are forbidden under `components/console/hr` (`tests/theme-literals.test.mjs`); colour only through `var(--fs-*)` tokens defined in `app/themes.css` for all three themes (cobalt, night, bureau), each with an entry in `tests/theme-contrast.test.mjs` (4.5:1).
- New `lib/*.ts` files and new `tests/*.mjs` are registered in `scripts/check-isolated.mjs` (file list and test run list).
- Org chart contract change is additive only; `docs/CONSOLE_API.md` and `docs/console_openapi.json` (`python -m scripts.export_console_openapi`) are updated in the same task.
- `csa/hr/process_routes.py` must not carry `from __future__ import annotations`. A new HR path is added to the allowed set in `tests/test_hr_persistence.py`.
- Writes: human session, CSRF, `Idempotency-Key`; `manage_people` or owner; non-HR callers get 404 `resource_unavailable`.
- Focus lands inside the dialog, notice or heading, never body; `aria-describedby` points at a unique id; errors go through `processSaveError` / `processLoadError`.
- Migration, if any, is `0637` (HR range 0630-0699; bump `EXPECTED_SCHEMA_VERSION` in `apps/gateway/main.py` and the guard tests).
- English only: defaults send `title_ms` and `description_ms` as null; nothing in the UI shows an empty Malay field for them.
- Max one concern per commit; CI is the merge gate; locally run the changed files' tests and `make lint`.

## Review Focus

1. A directory viewer (not HR, not the person's manager) must never receive a leaving date or the legal entity: the org chart shows "starting" to everyone but "leaving" only to HR and the person's own manager (server-side fields, not a client hide).
2. A cell, name or title starting with `=`, `+`, `-`, `@`, tab or CR in the CSV must not execute as a formula in a spreadsheet; names with commas, quotes and newlines must round-trip.
3. Two clicks, a double submit, a retried request or a company that already has a template of that kind must not create duplicate default templates (which would tie and raise conflicts), and a failure halfway must not leave one default created without the other.
4. With the switch off the create-defaults route refuses and the UI shows no button; turning the gate on must not require a code change in the content.
5. A loop in the reporting lines, a manager who has left or is outside the chart, a person with no manager, 300 people in one department, and a department with many colours-worth of siblings must all render, stay navigable by keyboard, and never drop a person.

---

### Task 1: Org node contract (backend, additive)

**Files:**
- Modify: `csa/hr/people_contracts.py:145-155`, `csa/hr/people_rules.py:160-192`
- Modify: `docs/CONSOLE_API.md` (org chart section, ~line 217), `docs/console_openapi.json` (regenerate)
- Test: `tests/test_hr_people_rules.py:142-181`, `tests/test_hr_people.py:1826-1858` (update the exact-keys assertion at ~1851), `tests/test_console_contract_snapshot.py`

**Interfaces:**
- Produces `OrgNode` with the existing five fields plus: `department_id: UUID | None`, `status: Literal["active","starting","leaving"]` (`left` never appears), `start_date: date | None`, `last_day: date | None`, `legal_entity_reference: str | None`. Values come straight from the already-loaded `HrPersonOut`; no new SQL, so a non-HR viewer receives `last_day` and `legal_entity_reference` only where the existing row rules already return them (HR, the person, their direct manager); otherwise null and `status` reads `active` for a leaving person.
- Consumes `org_chart(people)` in `people_rules.py` (pure).

- [ ] **Step 1: Write failing tests** in `test_hr_people_rules.py`: a starting person carries `status == "starting"` and `start_date`; a person with `last_day` set (row visible) carries `status == "leaving"` and `last_day`; `department_id` is passed through; a person whose row hides `last_day` and the employment profile has `last_day is None`, `legal_entity_reference is None`. In `test_hr_people.py`: an HR viewer sees all five new keys populated; a directory viewer (`is_active` people only) sees `legal_entity_reference` and `last_day` as null for a leaving person while `status` is `active` (pins Review Focus 1).
- [ ] **Step 2: Run them, expect FAIL** (`.venv/bin/python -m pytest tests/test_hr_people_rules.py tests/test_hr_people.py -k "org" -q`).
- [ ] **Step 3: Extend `OrgNode` and `org_chart()`**; update the exact-key assertion at `test_hr_people.py:1851` to the ten keys.
- [ ] **Step 4: Update `docs/CONSOLE_API.md`, regenerate `docs/console_openapi.json`**, run `tests/test_console_contract_snapshot.py`.
- [ ] **Step 5: Run the four test files green; commit** `feat(hr): org chart nodes carry department id, status, dates and legal entity`.

### Task 2: Org chart library (filters, search, expand state, tint, CSV)

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/hr-org-chart.ts`, `tests/hr-org-chart.test.mjs`
- Modify: `lib/hr-people-api.ts:60-67` (`OrgNode` gains the five optional fields), `scripts/check-isolated.mjs` (register both)
- Test: `tests/hr-people.test.mjs:132-160` must stay green (`orgTree` unchanged).

**Interfaces:**
- Consumes `orgTree(chart)` from `lib/hr-people.ts:221`.
- Produces in `lib/hr-org-chart.ts` (pure, no DOM):
  `filterOrgChart(chart: OrgChart, f: {department_id?: string; legal_entity?: string; query?: string}): OrgChart` (matches by name or title, trimmed, case-insensitive; keeps the manager chain of every match so a hit is never orphaned; legal entity compared trimmed and case-insensitive per spec ruling 8);
  `orgFilterChoices(chart): {departments: {id: string; name: string}[]; legalEntities: string[]}` (distinct, sorted, legal entities empty when no node carries one);
  `orgMarker(node): "starting" | "leaving" | null`;
  `departmentTint(departmentId: string | null): 1|2|3|4|5|6|null` (FNV-1a mod 6 like `tintIndex` in `lib/customer-identity.ts:30`; null for no department);
  `orgChartCsv(chart: OrgChart, opts: {includeLegalEntity: boolean}): string` (header row, one row per person: name, job title, department, manager name, status, start date, last day, legal entity only when `includeLegalEntity`; RFC 4180 quoting; a field that starts with `=`, `+`, `-`, `@`, tab or CR gets a leading apostrophe; CRLF line ends; UTF-8, no BOM added here).

- [ ] **Step 1: Write failing tests**: filter keeps ancestors of a match; empty filter returns the same nodes; legal-entity filter is case- and space-insensitive; `orgMarker` for a starting and a leaving node and for neither; `departmentTint` is stable across calls and null for null; CSV for a name `=cmd|' /C calc'!A0`, a title with a comma and a quote, a name with a newline, and a node with no manager; `includeLegalEntity: false` omits the column.
- [ ] **Step 2: Run them, expect FAIL** (`node --test tests/hr-org-chart.test.mjs` from the web dir).
- [ ] **Step 3: Implement `lib/hr-org-chart.ts`** and the type additions.
- [ ] **Step 4: Register in `check-isolated.mjs`; run `npm run check:isolated`-equivalent used in CI plus the two test files; commit** `feat(console): org chart filters, search, markers, tint and CSV helpers`.

### Task 3: Department tint tokens

**Files:**
- Modify: `app/themes.css` (all three themes), `tests/theme-contrast.test.mjs` (`COLOUR_TOKENS` ~lines 35-51 and `PAIRS` ~91-93)
- Test: `tests/theme-contrast.test.mjs`, `tests/theme-literals.test.mjs`

**Interfaces:**
- Produces `--fs-dept-1-bg` … `--fs-dept-6-bg` and `--fs-dept-1-text` … `--fs-dept-6-text` in cobalt, night and bureau, mirroring the `--fs-customer-1..4` precedent. Six pairs each reach 4.5:1 in every theme. Colour is never the only cue (Task 4 always prints the department name).

- [ ] **Step 1: Add the tokens to `COLOUR_TOKENS` and `PAIRS` first; run the contrast test, expect FAIL** (missing tokens).
- [ ] **Step 2: Define the twelve tokens per theme** so every pair passes; no new literal outside `themes.css`.
- [ ] **Step 3: Run contrast and literals tests green; commit** `feat(console): department tint tokens in all three themes`.

### Task 4: The org chart view

**Files:**
- Modify: `components/console/hr/OrgChart.tsx`, `components/console/hr/PeopleTab.tsx:67-81` (`OrgChartView`), the print stylesheet in the console's existing global CSS (tokens only, hide console chrome, show the whole tree expanded)
- Create: `tests/test_hr_org_chart_browser.py`
- Modify: `tests/test_hr_access_browser.py:400-413` (`RecordsWorld.answer` org chart payload gains the new fields, keeping the LeavingForm flow green)

**Interfaces:**
- Consumes Task 2 helpers and Task 3 tokens; `StatusLabel` (`fs/StatusLabel.tsx`) for the Starting and Leaving markers; props unchanged: `{chart, owner, hrefFor}` plus `canSeeLegalEntity: boolean` derived from the chart (any node with a legal entity).
- Produces behaviour: each node is a collapsible branch (a real `<button aria-expanded>`, default expanded to depth 2, state kept while filtering); a department chip with the tint and the department name; the Starting or Leaving marker; "No manager" and "Manager not in the chart" labels on roots instead of silence; the loop notice kept; toolbar with department select, legal-entity select (only when `legalEntities` is non-empty), search, Expand all, Collapse all, Print (`window.print()`), Download CSV (`Blob` + object URL, `includeLegalEntity` only when choices exist, file name `org-chart.csv`); empty and no-match states with a Clear filters action; focus lands on the result heading after a filter change announced through `aria-live="polite"` text "N people shown".

- [ ] **Step 1: Write the failing browser test** in a new `RecordsWorld` subclass (synthetic names): 12 people in 3 departments with one loop pair, one person whose manager left, one starting and one leaving person, a department with 40 members, a legal entity on some. Assertions: every person appears (count text equals chart size); collapse hides descendants and keeps `aria-expanded` truthful; keyboard Enter and Space toggle; department filter and search keep the ancestor chain; markers show; loop notice appears for the owner only; CSV download via `page.expect_download` has the header and no row starting with an unescaped `=`; the legal-entity select is absent for a world without legal entities; runs at phone width and in all three themes (reuse `THEMES` and `WIDTHS` from `tests/test_team_browser.py`).
- [ ] **Step 2: Run it, expect FAIL.**
- [ ] **Step 3: Implement the component, `OrgChartView` wiring and the print stylesheet.**
- [ ] **Step 4: Run the new browser test, `test_hr_access_browser.py`, `test_hr_restyle_browser.py`, the node suite, lint and type check; commit** `feat(console): org chart with collapsible branches, department colour, markers, filters, print and CSV`.

### Task 5: Default template content, sources and the review document

**Files:**
- Create: `csa/hr/default_templates.py` (data only, no logic beyond a loader), `docs/hr/default-templates-sources.md` (the committed research: official URL, form or portal name, the timing wording as the source states it, date checked, confidence), `scripts/render_default_templates_review.py`, `docs/hr/DEFAULT_TEMPLATES_REVIEW.md` (generated, with a sign-off block: reviewer name and role, date, "approved / changes needed")
- Test: `tests/test_hr_default_templates.py`

**Interfaces:**
- Produces `DEFAULT_TEMPLATES: tuple[TemplateCreate, ...]` (one onboarding, one offboarding, both validated by the existing `TemplateCreate` model from `csa/hr/process_models.py`) and `DEFAULT_TEMPLATE_SOURCES: dict[str, SourceNote]` keyed by `"<kind>:<ordinal>"` with `{body, url, checked_on: date, confidence}`.
- Content rules: step list and owners from spec section 7, adjusted by the research and by two corrections found in the code: the "bank and statutory numbers" step has no auto-check (the check `personal_details_complete` only covers contact fields), and no step uses `owner_role: "person"` (needs a real employee id). Input for the content: `docs/hr/default-templates-sources.md` is the committed copy of the official-source research (the controller supplies the draft at dispatch). It found: no statutory probation rule and no statutory rule on return of company property (so those steps carry no statutory reference and say so); a written contract is mandatory only for some fixed-term or piece-work contracts, so the contract step is good practice, not a legal duty for everyone; KWSP (EPF) pages could not be read (HTTP 403), Sarawak and current Sabah texts could not be read, and per-employee PERKESO or EIS leaver notices were not found, so those steps carry "timing not confirmed on an official page" notes; the EIS vacancy notification is not in force, so it is not a default step. Allowed `auto_check` values only: `account_linked`, `personal_details_complete`, `access_removed`, `document_uploaded:<kind>`. Onboarding anchors `start_date` or `probation_end`; offboarding anchors `last_day`, offsets `<= 0` for the leaving person's own steps; the probation step is anchored on `probation_end` and shows "waiting for a date" until HR sets it. Steps the research found as applicable only to some people (foreign worker, HRD Corp, a PDPA notice) are optional (`required: false`) with a plain note saying who it applies to. `title_ms` and `description_ms` are never set.
- The review document is generated from the data file so it cannot drift, lists every step with owner, due offset in plain words, required or optional, the reference note and source, and ends with the sign-off block. A test fails if the committed review document differs from what the script renders.

- [ ] **Step 1: Write failing tests**: every default validates as `TemplateCreate`; every step whose note names a statutory body has a `SourceNote` with an `https` URL on an allowlisted official domain list defined in the test (kwsp.gov.my, perkeso.gov.my, hasil.gov.my, mohr.gov.my, jtksm.mohr.gov.my, pdp.gov.my, hrdcorp.gov.my, esd.imi.gov.my and any domain the research actually cited, each justified in the sources doc) and `checked_on` not later than today; no note or description contains `%`, `RM` followed by a digit, or a digit adjacent to a currency word; `title_ms` and `description_ms` are all None; offboarding offsets `<= 0` for steps owned by the leaving person (the engine warns `step_after_last_day` only for those), while HR- or manager-owned steps may fall after the last day where the official source gives a period after it; anchors and owner roles are in the allowed sets; `owner_role != "person"`; the committed review document equals the rendered one.
- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Write the content from `docs/hr/default-templates-sources.md`**; where the research could not confirm a timing on an official page, the step carries no timing claim and its source note says "timing not confirmed on an official page: reviewer to confirm" (the reviewer resolves it before the gate opens).
- [ ] **Step 4: Render the review document, run tests green; commit** `feat(hr): Malaysian default template content with sources and a generated review document`.

### Task 6: Create-defaults action, gated

**Files:**
- Modify: `csa/hr/process_routes.py`, `csa/hr/process_models.py`, `csa/hr/processes.py`, `csa/hr/api.py` (body cap for the new path), `deploy/staging-rollout.env` and `deploy/production-rollout.env` only if the switch is read from the deploy files (follow the existing switch pattern; the switch ships OFF in both)
- Possibly create: `db/migrations/0637_hr_template_defaults.sql` (only if the atomic variant below is chosen; then bump `EXPECTED_SCHEMA_VERSION` and the migration guard tests)
- Modify: `tests/hr_process_world.py` (`ROUTES`, new stub `POST hr/templates/defaults`), `tests/test_hr_persistence.py` (allowed path `/console/v1/hr/templates/defaults`), `docs/CONSOLE_API.md`, `docs/console_openapi.json`
- Test: `tests/test_hr_process_defaults.py` (new), `tests/test_hr_process_api.py` (CSRF and idempotency row for the route)

**Interfaces:**
- Consumes `DEFAULT_TEMPLATES` (Task 5) and the existing `hr_template_apply` (migration 0631).
- Produces `POST /console/v1/hr/templates/defaults` (201) with an empty JSON body and an `Idempotency-Key`, returning `{created: [{kind, template_id, version, name}], skipped: [{kind, reason: "already_present"}], duplicate: bool}`; the service function `HrProcesses.create_default_templates(access, key)`; and a read flag `defaults_available: bool` on the templates list response (`{available: bool, created: bool}` under a `defaults` key, additive) so the UI knows whether to show the button.
- Behaviour: refused with 404 `resource_unavailable` when the gate switch is off (and `defaults.available` is false); under the tenant's `hr-operations` lock, a kind that already has any active template is skipped, not duplicated; both templates are created in one transaction or none (choose either composing `hr_template_apply` calls inside one Python transaction with keys derived from the request key, or a `0637` definer function; record the choice as a ruling in the ledger and prefer the first when the service already runs writes in one transaction); the same key and body returns the stored result with `duplicate: true`; each created template writes the existing `hr.template_created` audit row.

- [ ] **Step 1: Write failing tests**: switch off -> 404 and `defaults.available == false` on the list; switch on, empty company -> both created, two audit rows; second call with a new key -> both skipped `already_present`, nothing new; same key twice -> `duplicate: true`; a company with one active onboarding template -> only offboarding created; forced failure on the second template -> nothing created; non-HR caller -> 404; capability and assistant sessions refused; CSRF missing refused.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement the route, models, service and the switch (OFF in both rollout env files); update the world stub, the allowed-path set, docs and OpenAPI.**
- [ ] **Step 4: Run the new tests, `test_hr_persistence.py`, `test_hr_process_api.py`, the contract snapshot and the rollout asset tests; commit** `feat(hr): create the default templates once, behind a review switch`.

### Task 7: Create-defaults UI

**Files:**
- Modify: `components/console/hr/TemplateList.tsx` (empty-state area ~lines 55-60), `lib/hr-process-api.ts` (client method near the template writes ~354-362), `lib/hr-process-copy.ts` (`PROBLEMS` sentences for the new refusals), `tests/test_hr_templates_browser.py`, `tests/hr_process_world.py`
- Test: node tests for the copy; browser test as above.

**Interfaces:**
- Consumes `defaults: {available, created}` from the template list and `POST /templates/defaults` (Task 6).
- Behaviour: with `available` false there is no button and no hint; with `available` true and nothing created, a "Create the default templates" button opens a dialog listing the steps of both templates (title, owner in plain words, when it is due in a plain sentence, required or optional, source name and "checked on" date), with the statement that these are reminders with references, not filings, and that HR can edit them; confirming posts once (stable `Idempotency-Key` per open dialog, button disabled while pending), then shows a notice with what was created and links to the two templates; "already present" shows a plain sentence; a failure goes through `processSaveError` and keeps the dialog open; focus lands inside the dialog, then on the notice.

- [ ] **Step 1: Write failing browser tests** (button absent when off; dialog content; one POST on a double click; created notice; already-present sentence; error keeps the dialog; phone width) and copy tests for the new `PROBLEMS` sentences.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the browser and node tests, lint, type check; commit** `feat(console): create the default templates from the template list`.

### Task 8: Docs, spec corrections and retest rows

**Files:**
- Modify: `docs/superpowers/specs/2026-11-16-hr-processes-design.md` (English-only decision; "bank and statutory numbers" has no auto-check; the review-gate text names the generated review document and the switch), `docs/hr/SETUP.md` ("First templates": how the defaults work, the gate, that turning it on makes the onboarding default start real processes for the next person added), `docs/hr/PROCESSES_RETEST.md` (rows for the org chart and the defaults), `docs/PROGRESS.md`, `docs/hr/PROGRESS.md`, `db/migrations/README.md` (only if 0637 exists)
- Test: the existing docs and guard tests (`make lint` and the docs tests the repo runs).

- [ ] **Step 1: Update the docs** with the exact behaviour built; add `docs/hr/DEFAULT_TEMPLATES_REVIEW.md` to the SETUP gate steps (reviewer reads, signs, owner flips the switch through a reviewed PR to `deploy/*-rollout.env`).
- [ ] **Step 2: Run the docs and lint checks; commit** `docs(hr): PR 4 behaviour, the review gate and retest rows`.

## Not in this PR

- The workspace assistant understanding HR processes (what is outstanding for X's onboarding, my tasks, mark a task done) in Malay and English: its own PR after this one (spec section 9, slice 1c), with Malay and English smoke cases. Existing HR people actions already have Malay cases.
- Renaming or translating the console UI.
- Positions and vacancies in the org chart (recruitment phase).
- Department colours stored per department (a later column if the owner wants to choose them).
