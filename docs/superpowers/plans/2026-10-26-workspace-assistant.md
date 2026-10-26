# Workspace Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One floating assistant on every console tab that answers with tab context, carries out three actions (email draft export, training request submit, held-reply decision) through server-issued proposals confirmed on the same path as the tab's own buttons, navigates between tabs, and resolves people through a basic company directory.

**Architecture:** The existing workspace threads and turns become the conversation store. A Groq planner turns a message plus an untrusted `page_context` into one typed proposal. Mutating proposals are rows in a new `console_proposals` registry bound to user, login session, tenant, payload hash, expected version and a 15-minute expiry; confirming executes through the existing HR, email and inbox services. The three tab actions create and confirm proposals through the same registry. A company directory view exposes three fields to tenant staff; the planner never sees it. The dock lives in `AppShell` and reads page context published by each workspace.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async, Pydantic v2, PostgreSQL 16 (staged RLS), `GroqClient.json_completion`, pytest + Testcontainers, Next.js App Router, `node --test`, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-26-workspace-assistant-design.md`

## Global Constraints

- psycopg 3 async only; SQL as parameterized files under `db/sql/` loaded through `csa.core.db.q`; Pydantic contracts with `extra="forbid"`; surface errors, no silent fallbacks; only `csa/graph/` imports LangGraph (nothing here does).
- Migration `db/migrations/0460_workspace_assistant.sql` is additive; RLS enabled, never FORCE. `EXPECTED_SCHEMA_VERSION` becomes `"0460"` in `apps/gateway/main.py`, `tests/test_migrate.py` and `tests/test_hr_persistence.py`.
- Registry kinds are exactly `email_export`, `training_submit`, `inbox_decision`; proposal TTL is 15 minutes; a proposal executes once; confirm requires the same `login_session_id`, CSRF, status `requested`, not expired, matching `payload_sha256` and matching `expected_version` when set.
- Gate statuses: inbox decisions require `customer_support` in `active` or `suspended`; training submit and people require `hr` `active`; email requires `staff_email` `active` plus `email_settings.enabled`; training proposals also require the tenant's `hr_assistant` switch. `read_only` is denied by the workspace router (existing rule stays).
- Planner behind `WORKSPACE_ASSISTANT_ENABLED: bool = False`; when true, `GROQ_API_KEY` and `GROQ_MODEL` are used through `GroqClient(api_key, model, httpx.AsyncClient())`. The planner prompt never receives directory listings or record bodies beyond a server-built redacted summary.
- Company directory exposes exactly `employee_id, display_name, job_title, work_email`; search limited to 10 candidates; `people/me` reason codes exactly `no_employee_link`, `no_manager`, `package_inactive`.
- Error details: `package_inactive` (403), `proposal_expired` (409), `proposal_consumed` (409), `proposal_session_mismatch` (403), `proposal_stale` (409), `planner_unavailable` (503), `planner_invalid_output` (502).
- Audit actions: `workspace.proposal.created|confirmed|executed|cancelled`, `workspace.people.lookup` (details `{"candidates": n}` only).
- Frontend files outside `app/` and `components/console/` must be listed in `CSA_WebConsole/csa-console/web/scripts/check-isolated.mjs`.
- Done means `NO_LOCAL_ENV=1 ENV=ci make lint test`, `tests/test_packaged_console.py -m slow`, the isolated frontend check, and `tests/test_migrate.py::test_make_reset_only_disposable_container` pass on the final source.

## Review Focus

1. A second browser session of the same user must not be able to confirm a proposal created in the first (login session binding). Pinned in Task 3.
2. Confirming a held-reply decision after the conversation moved (version changed) must return 409 `proposal_stale`, not act. Pinned in Task 4.
3. A user without an employee link asking "my boss" must get `no_employee_link`, never another tenant's or user's manager. Pinned in Task 5.
4. Planner output naming a kind the user's packages do not allow (for example `training_submit` without `hr`) must be refused with 403 `package_inactive` before any registry row exists. Pinned in Task 7.
5. Navigating while a proposal is `requested` must cancel it; the stale card must not be confirmable afterwards. Pinned in Task 10 (browser) and Task 3 (cancel endpoint).

---

### Task 1: Migration 0460 and schema bump

**Files:**
- Create: `db/migrations/0460_workspace_assistant.sql`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION`), `tests/test_migrate.py:19` and its assertion, `tests/test_hr_persistence.py` (version assertion)
- Test: `tests/test_migrate.py`, `tests/test_workspace_registry.py` (new; schema tests only in this task)

**Interfaces:**
- Produces table `console_proposals(id uuid pk default uuid_generate_v4(), client_id uuid not null references clients(id), user_id uuid not null references console_users(id), login_session_id uuid not null, thread_id uuid null, kind text not null check (kind in ('email_export','training_submit','inbox_decision')), payload jsonb not null check (jsonb_typeof(payload)='object'), payload_sha256 text not null check (payload_sha256 ~ '^[0-9a-f]{64}$'), expected_version text null, status text not null check (status in ('requested','confirmed','executed','expired','cancelled')), created_at timestamptz not null default now(), expires_at timestamptz not null, executed_at timestamptz null, result jsonb null, check (expires_at > created_at and expires_at <= created_at + interval '15 minutes'), foreign key (login_session_id, user_id) references console_sessions(id, user_id))`, tenant RLS policy `tenant_isolation`, index `(client_id, user_id, status)`, grants SELECT/INSERT/UPDATE to `csa_app`, REVOKE from PUBLIC/csa_app/csa_ro/csa_identity first.
- Relaxes `console_workspace_turns`: drop the `action`, `page_key` and the combined CHECK constraints; add `check (action in ('answer','navigate','directory_lookup','email_compose','training_submit','inbox_decision','clarification','page_guidance','local_order_read'))` and `check (page_key in ('workspace_home','business_orders','business_order','overview','services','inbox','knowledge_base','business','hr','email','activity','chat'))`; `resource_id` stays nullable text.
- View `company_directory(client_id, employee_id, user_id, display_name, job_title, work_email)` as `security_barrier`, selecting active `hr_employees` left-joined to `hr_employee_accounts`, filtered by `client_id = nullif(current_setting('app.client_id',true),'')::uuid`; owned by the migration role so it reads through the HR RLS; `GRANT SELECT ... TO csa_app`.

- [ ] **Step 1: Write failing schema tests** in `tests/test_workspace_registry.py` with `pytest_plugins = ["tests.test_console_sessions"]`:

```python
async def test_proposals_table_rls_grants_and_checks(sessions):
    t = sessions.tenants[0]
    rows = await sessions.q("SELECT has_table_privilege('csa_ro','public.console_proposals','SELECT') r, has_table_privilege('csa_app','public.console_proposals','UPDATE') u")
    assert rows[0]["r"] is False and rows[0]["u"] is True
    with pytest.raises(Exception, match="console_proposals_kind_check|violates check"):
        await sessions.q("INSERT INTO console_proposals (client_id,user_id,login_session_id,kind,payload,payload_sha256,status,expires_at) VALUES (%s,%s,%s,'nope','{}','0'*64,'requested',now()+interval '1 minute')", t, sessions.admin_id, uuid4())

async def test_company_directory_view_is_tenant_scoped(sessions):
    # insert two employees in tenant 0 and one in tenant 1 as owner; under app.client_id = tenant 0 only two rows are visible, with the three fields
```

Complete the second test with a `psycopg` connection as `csa_app` (`app_dsn` fixture from `tests.conftest` as `tests/test_console_sessions.py` uses) that runs `SET LOCAL app.client_id` and selects from the view.

- [ ] **Step 2: Run to verify failure.** `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_workspace_registry.py` → FAIL (relation does not exist).
- [ ] **Step 3: Write the migration** per Interfaces; header comment with preconditions (`0450`) and rollback (drop view, drop table, restore the two CHECKs).
- [ ] **Step 4: Bump the three expected versions; run** `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_migrate.py tests/test_workspace_registry.py tests/test_console_workspace.py` → PASS (existing workspace tests still pass against relaxed CHECKs).
- [ ] **Step 5: Commit** `feat(workspace): add proposal registry and company directory schema (0460)`.

---

### Task 2: Contracts for page context, proposals and registry

**Files:**
- Modify: `csa/workspace/contracts.py`
- Test: `tests/test_workspace_contracts.py` (append)

**Interfaces (all `_Contract`, `extra="forbid"`):**
```python
TabKey = Literal["overview","services","inbox","knowledge_base","business","hr","email","activity","chat"]
class RecordRef(_Contract): kind: Literal["conversation","approval","training_request","draft","order","employee"]; id: str = Field(min_length=1, max_length=100); version: int | None = None
class PageContext(_Contract): tab: TabKey; section: str | None = Field(default=None, max_length=40); record: RecordRef | None = None
class WorkspaceTurnInput(_Contract): request_key: UUID; body: str (1..4000, nonblank); page_context: PageContext | None = None; page_hint: PageHint | None = None  # page_hint kept for the legacy page-guidance path
class AnswerProposal: action: Literal["answer"]; markdown: str (1..8000); reads: list[Literal["read_current_record","my_profile","my_packages"]] (max 3)
class NavigateProposal: action: Literal["navigate"]; tab: TabKey; section: str|None; record: RecordRef|None; resume: bool = False
class DirectoryLookupProposal: action: Literal["directory_lookup"]; query: str (1..120)
class EmailComposeProposal: action: Literal["email_compose"]; recipient_employee_id: UUID|None; recipient_address: str|None (3..254); subject: str (1..200); instructions: str (0..1000); language: Literal["auto","en","ms"]="auto"  # exactly one recipient field set
class TrainingSubmitProposal: action: Literal["training_submit"]; request_id: UUID|None; title: str|None (1..160); learning_objective: str|None (1..1000)  # request_id xor (title and learning_objective)
class InboxDecisionProposal: action: Literal["inbox_decision"]; approval_id: int (ge=1); conversation_id: UUID; decision: Literal["approve","reject"]; expected_version: int (ge=0)
class ClarificationProposal: action: Literal["clarification"]; question: str (1..500); options: list[ClarificationOption] (0..5)  # ClarificationOption{label, value}
WorkspaceProposal = Annotated[Union[...all above..., GuidanceProposal, OrderReadProposal], Field(discriminator="action")]
ProposalKind = Literal["email_export","training_submit","inbox_decision"]
class ProposalCard(_Contract): proposal_id: UUID; kind: ProposalKind; title: str; summary: str; fields: list[CardField]; expires_at: AwareDatetime; expected_version: str|None  # CardField{label, value}
class ProposalOut(_Contract): id: UUID; kind: ProposalKind; status: Literal["requested","confirmed","executed","expired","cancelled"]; card: ProposalCard; result: dict|None; created_at: AwareDatetime; expires_at: AwareDatetime
class PersonOut(_Contract): employee_id: UUID; display_name: str; job_title: str|None; work_email: str|None
class MyPeopleOut(_Contract): employee_id: UUID|None; manager: PersonOut|None; reason: Literal["no_employee_link","no_manager","package_inactive"]|None
```
- Existing `WorkspaceTurnOut.result` gains the union member `ProposalResult(action: Literal["proposal"], proposal: ProposalOut)` and `AnswerResult(action: Literal["answer"], markdown: str)`, `NavigateResult(action: Literal["navigate"], tab, section, record, resume)`, `DirectoryResult(action: Literal["directory_lookup"], candidates: list[PersonOut])`, `ClarificationResult(action: Literal["clarification"], question, options)`; `WorkspaceActionResult` union extended accordingly.

- [ ] **Step 1: Write failing tests**: `EmailComposeProposal` with both recipient fields set raises; with neither raises; `TrainingSubmitProposal` with `request_id` and `title` raises; `PageContext(tab="hr", record=RecordRef(kind="training_request", id="x"))` validates; `RecordRef(kind="customer")` raises; `WorkspaceProposal` discriminator parses each kind from a dict; `ProposalCard` forbids extra keys.
- [ ] **Step 2: Run** `uv run pytest -q tests/test_workspace_contracts.py` → FAIL.
- [ ] **Step 3: Implement the models.**
- [ ] **Step 4: Run** → PASS; `NO_LOCAL_ENV=1 ENV=ci make lint` clean.
- [ ] **Step 5: Commit** `feat(workspace): typed page context, proposal union and registry contracts`.

---

### Task 3: Proposal registry

**Files:**
- Create: `csa/workspace/registry.py`, `db/sql/workspace/proposal_insert.sql`, `proposal_get.sql` (FOR UPDATE), `proposal_set_status.sql`, `proposal_cancel_thread.sql`, `proposal_expire.sql`
- Test: `tests/test_workspace_registry.py`

**Interfaces:**
```python
class ProposalError(HTTPException)  # detail in {proposal_expired, proposal_consumed, proposal_session_mismatch, proposal_stale, proposal_not_found}
class ProposalExecutor(Protocol):
    kind: ProposalKind
    async def card(self, conn, actor: ConsoleActor, client_id: UUID, payload: dict) -> tuple[ProposalCard_fields, expected_version: str|None]  # validates payload for the kind, loads the record under the user's authorization, returns title/summary/fields and the version to bind
    async def execute(self, conn, request: Request, actor: ConsoleActor, client_id: UUID, payload: dict) -> dict  # performs the action through the owning service; returns its receipt as a JSON-able dict
class ProposalRegistry:
    def __init__(self, executors: Mapping[ProposalKind, ProposalExecutor]) ...
    async def create(self, conn, actor: ConsoleActor, client_id: UUID, kind: ProposalKind, payload: dict, *, thread_id: UUID|None) -> ProposalOut   # sha256 over canonical JSON; expires_at = now + 15 min; audit workspace.proposal.created
    async def get(self, conn, actor, client_id, proposal_id) -> ProposalOut   # 404 proposal_not_found unless owner
    async def confirm(self, conn, request, actor, client_id, proposal_id) -> ProposalOut  # locks row; checks login_session_id == actor.session_id (403 proposal_session_mismatch), status requested (409 proposal_consumed), not expired (409 proposal_expired, sets status expired), payload hash unchanged; re-runs executor.card() and compares expected_version (409 proposal_stale); sets confirmed → executes → executed with result; audits confirmed and executed
    async def cancel(self, conn, actor, client_id, proposal_id) -> ProposalOut  # requested → cancelled; audit cancelled
    async def cancel_thread(self, conn, actor, client_id, thread_id) -> int  # cancels all requested rows for the thread
```
- Tests use a fake executor registered under `training_submit` so the registry is tested without HR.

- [ ] **Step 1: Write failing tests**: create returns `requested` with 15-minute expiry and a card; confirm from a second browser session of the same user → 403 `proposal_session_mismatch`; confirm after `UPDATE console_proposals SET expires_at = now() - interval '1 second'` → 409 `proposal_expired` and row status `expired`; confirm twice → second is 409 `proposal_consumed`; executor reporting a different `expected_version` at confirm → 409 `proposal_stale`; `cancel_thread` leaves non-thread rows untouched; audit rows `workspace.proposal.created/confirmed/executed` exist with the proposal id as target.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; lint.
- [ ] **Step 5: Commit** `feat(workspace): proposal registry with session-bound single-use confirmation`.

---

### Task 4: Executors and proposal routes

**Files:**
- Create: `csa/workspace/executors.py`
- Modify: `csa/console/workspace.py` (routes), `csa/console/inbox.py` (export a reusable `decide_approval(conn, client_id, actor, approval_id, decision, text) -> InboxResult` extracted from the `decide` handler body), `apps/gateway/main.py` (register `app.state.proposal_registry`)
- Test: `tests/test_workspace_registry.py`

**Interfaces:**
- `TrainingSubmitExecutor`: payload `TrainingSubmitProposal`; `card()` loads the request via `HrService.training(session, request_id)` (building `HrSession` from the actor and the request cookies exactly as `csa/hr/api.py::_session` does; factor that into `csa/hr/api.py::hr_session_from(request, actor, tenant_id) -> HrSession`), binds `expected_version=str(projection.version)`; when `request_id` is None, `execute()` first `save_draft(session, TrainingDraftInput(...), key)` then `transition(session, id, TrainingCommandInput(action=SUBMIT, expected_version=...), key2)`; checks `hr_assistant` switch via `HrService._require_capability` equivalent public call.
- `EmailExportExecutor`: payload `{"draft_id": uuid}`; `card()` loads the draft (recipient, subject) through `EmailService` read; `execute()` calls `export_draft(session, draft_id, key)`.
- `InboxDecisionExecutor`: payload `InboxDecisionProposal`; `card()` reads the approval and the conversation's `inbox_version` (binds it as `expected_version`); `execute()` calls the extracted `decide_approval`.
- Routes (router prefix `/workspace`, existing `workspace_actor` + `_workspace_access`): `POST /proposals` body `{kind, payload, thread_id?}` → 201 `ProposalOut` (direct creation for tabs); `GET /proposals/{id}`; `POST /proposals/{id}/confirm` → `ProposalOut`; `POST /proposals/{id}/cancel`. Package gates per kind applied inside the registry create via `require_package` with the statuses from Global Constraints.

- [ ] **Step 1: Write failing HTTP tests** (SessionWorld app with HR and email routers mounted as `tests/test_console_services.py::services_app` does, plus `app.state.proposal_registry`): training submit end to end (draft via HR API, then proposal, then confirm → `TrainingReceipt` status submitted); inbox decision end to end using `tests/test_console_inbox.py::held` to create a pending approval, confirm → approval approved; bump `inbox_version` between create and confirm → 409 `proposal_stale`; email export with `tests/test_email_service.py::FakeGmail` → `gmail_draft_id` set; a tenant without `hr` creating `training_submit` → 403 `package_inactive`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** plus `tests/test_console_inbox.py tests/test_hr_training.py tests/test_email_service.py` → PASS; regenerate the OpenAPI snapshot; lint.
- [ ] **Step 5: Commit** `feat(workspace): proposal executors and routes for the three actions`.

---

### Task 5: Company directory and people routes

**Files:**
- Create: `csa/workspace/directory.py`, `db/sql/workspace/people_search.sql` (`SELECT employee_id, display_name, job_title, work_email FROM public.company_directory WHERE client_id=%s AND (display_name ILIKE %s OR work_email ILIKE %s) ORDER BY display_name LIMIT 10`), `people_me.sql` (caller's employee row and manager's three fields via `hr_employees.manager_id`)
- Modify: `csa/console/workspace.py` (routes `GET /people?q=`, `GET /people/me`)
- Test: `tests/test_workspace_people.py`

**Interfaces:** `async def search_people(conn, client_id, query: str) -> list[PersonOut]`; `async def my_people(conn, client_id, user_id) -> MyPeopleOut`. Routes gated by `require_package(..., {"hr"})` → otherwise `MyPeopleOut(reason="package_inactive")` for `/people/me` and 403 `package_inactive` for search; `read_only` denied by the router; audit `workspace.people.lookup` with `{"candidates": n}`; per-session throttle 60/min using the identity throttle helper pattern (`IdentityService._throttle`) or a simple in-transaction counter table is NOT added: use an in-memory per-process limiter keyed by session id with a note that it is best-effort.

- [ ] **Step 1: Write failing tests**: staff of tenant 0 sees colleagues' three fields only (no `department`, no `manager`); tenant 1 employees never appear; `q=ali` matches `Ali Hassan` and `alia@…`; eleven matches return 10; `/people/me` for a user with no `hr_employee_accounts` row → `no_employee_link`; with a link but `manager_id` NULL → `no_manager`; with a manager → manager's three fields; without the `hr` package → `package_inactive`; `read_only` → 403.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; snapshot; lint.
- [ ] **Step 5: Commit** `feat(workspace): company directory search and my-manager lookup`.

---

### Task 6: Email compose

**Files:**
- Modify: `csa/email/models.py` (`ComposeRequest(recipient_employee_id: UUID|None, recipient_address: str|None, subject: str, instructions: str, language)`), `csa/email/service.py` (`async def compose(self, session, request: ComposeRequest, key: UUID) -> DraftOut`), `csa/email/api.py` (`POST /compose` → 201 `DraftOut`)
- Test: `tests/test_email_service.py`

**Interfaces:** `compose` resolves `recipient_employee_id` through `company_directory` (work_email; 422 `recipient_unresolved` when NULL) or validates `recipient_address` with the existing `EmailDraftInput.validate_recipient`; requires a mailbox row (`mailbox_not_connected` as today); uses an internal staff persona prompt (system text fixed in the module) that names the recipient by display name; `thread_id` NULL; `source="model"`.

- [ ] **Step 1: Write failing tests** in the email World: compose to an employee id → draft with the employee's work email; compose with both recipient fields → 422; no mailbox → `mailbox_not_connected`; `email_settings.enabled=false` → refused; export of the composed draft works through `export_draft`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS; snapshot; lint.
- [ ] **Step 5: Commit** `feat(email): compose an internal draft to a resolved recipient`.

---

### Task 7: Planner and turn execution

**Files:**
- Create: `csa/workspace/planner.py`
- Modify: `csa/core/settings.py` (`WORKSPACE_ASSISTANT_ENABLED: bool = False`), `apps/gateway/main.py` (`app.state.workspace_planner = WorkspacePlanner(GroqClient(...))` when enabled), `csa/workspace/service.py` (`create_turn` uses the planner when `page_context` is present; keeps the legacy `page_hint` path), `csa/console/workspace.py` (503 `planner_unavailable` when a `page_context` turn arrives and no planner is configured)
- Test: `tests/test_workspace_planner.py`

**Interfaces:**
```python
class PlannerFacts(_Contract): role: Role; packages: list[PackageState]; page_context: PageContext|None; record_summary: str|None; language: Literal["en","ms"]
class WorkspacePlanner:
    def __init__(self, client: JsonCompletionClient, *, timeout: float = 20.0) ...
    async def plan(self, message: str, facts: PlannerFacts) -> WorkspaceProposal   # json_completion(schema=PlannerOutput, system=SYSTEM_PROMPT, user=<facts + message>, timeout); invalid → raise PlannerInvalidOutput (502 planner_invalid_output)
```
- `create_turn` flow for `page_context` turns: build facts (role, active+suspended packages, redacted record summary via the owning service for `record.kind`), call the planner, then: `answer` → execute the typed reads, store `AnswerResult`; `navigate` → validate tab against role/packages (same rule as `lib/navigation.ts`), store `NavigateResult`; `directory_lookup` → `search_people`, store `DirectoryResult`; `clarification` → store; mutating kinds → gate check (`require_package` with the kind's statuses, `hr_assistant` switch for training) **before** `registry.create(...)`, store `ProposalResult`. Before planning, if the thread's previous turn had a different `page_context.tab` (or the tenant differs), call `registry.cancel_thread(...)` so navigation and tenant switches cancel stale `requested` proposals server-side. The turn row `action` is the proposal action, `page_key` the tab.

- [ ] **Step 1: Write failing tests** with a `ScriptedCompletion` client (as `tests/test_email_service.py::ScriptedModel`): each kind round-trips; invalid JSON → 502; `training_submit` for a tenant without `hr` → 403 before any registry row; `navigate` to `email` without `staff_email` → stored as a clarification-free `AnswerResult` explaining the package is not active (planner output is overridden server-side); the prompt sent to the client contains no `work_email` value from a seeded employee; Malay message yields `language="ms"` in facts.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** plus `tests/test_console_workspace.py` → PASS; snapshot; lint.
- [ ] **Step 5: Commit** `feat(workspace): planner-driven turns with gated proposals`.

---

### Task 8: HR assistant delegation

**Files:**
- Modify: `csa/hr/api.py` (`POST /assistant` delegates), `csa/hr/assistant.py` (thin adapter), `apps/gateway/main.py` (HR assistant wiring uses the workspace planner), `tests/test_hr_training.py` and any test asserting a direct `TrainingReceipt` from `/hr/assistant`

**Interfaces:** `POST /console/v1/hr/assistant` body unchanged; responses: reads and clarifications as before; a training submit returns `ProposalOut` (201); the HR tab confirms via `/console/v1/workspace/proposals/{id}/confirm`. `hr_chat_plans` is no longer written (table retained).

- [ ] **Step 1: Adapt the failing tests**: assistant submit → `ProposalOut` with `kind="training_submit"`; confirming it → receipt; `hr_assistant` switch off → 409 `agent_disabled` as today.
- [ ] **Step 2–5:** implement, run `tests/test_hr_*.py tests/test_workspace_*.py`, lint, snapshot, commit `refactor(hr): delegate the HR assistant to the workspace planner`.

---

### Task 9: Frontend page context and API client

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/page-context.tsx` (`PageContextProvider`, `usePublishContext(ctx: PageContext | null)`, `usePageContext()`), `tests/page-context.test.mjs`
- Modify: `lib/workspace-api.ts` (types `PageContext`, `ProposalOut`, `ProposalCard`, `PersonOut`, `MyPeopleOut`; functions `createProposal(kind, payload, threadId?)`, `confirmProposal(id)`, `cancelProposal(id)`, `searchPeople(q)`, `myPeople()`, `createWorkspaceTurn` accepting `page_context`), `tests/workspace-api.test.mjs` (new), `scripts/check-isolated.mjs` (register the new lib and test files)
- Test: the two node tests

- [ ] **Step 1: Write failing tests**: client paths and bodies (`/console/v1/workspace/proposals/<id>/confirm` with CSRF; `people?q=` encoded; `page_context` sent in the turn body); provider publishes and clears context (pure reducer test of the provider's state function exported for tests).
- [ ] **Step 2–5:** run, implement, `check:isolated`, commit `feat(console-web): page context provider and workspace proposal client`.

---

### Task 10: Assistant dock and proposal card

**Files:**
- Create: `components/console/AssistantDock.tsx`, `components/console/ProposalCard.tsx`
- Modify: `components/AppShell.tsx` (mount `<AssistantDock me packages />` inside `<main>` after `{children}`; wrap children in `PageContextProvider`), `lib/navigation.ts` (remove the `workspace` item), `app/(admin)/workspace/page.tsx` and `app/portal/workspace/page.tsx` (redirect to `/` and `/portal`), delete `components/console/WorkspaceAssistant.tsx`, trim `lib/workspace-paths.mjs` to the HR guidance helper and adjust `tests/workspace-paths.test.mjs`, `tests/nav-visibility.test.mjs` (Staff workspace no longer listed)

**Behaviour:** dock hidden for `read_only` and for platform admins on pages without `?client_id=`; collapsed button `data-testid="assistant-open"`; panel shows turns, current tab chip from `usePageContext()`, input, `ProposalCard` for `ProposalResult`, candidate list for `DirectoryResult` (click → sends a follow-up turn naming the `employee_id`), `NavigateResult` → `router.push` and, when `resume`, re-posts the original message with the new context; navigation with a `requested` card → `cancelProposal` first. `ProposalCard` renders title, summary, fields, expiry, `Confirm` (`data-testid="proposal-confirm"`) and `Cancel`; shows `ApiError.message` verbatim on failure; on success shows the result summary.

- [ ] **Step 1–3:** implement; `check:isolated` passes; nav test updated; commit `feat(console-web): floating assistant dock with proposal cards`.

---

### Task 11: Tabs publish context and confirm through proposals

**Files:**
- Modify: `components/console/InboxWorkspace.tsx` (publish `{tab:"inbox", record:{kind:"conversation", id, version}}` when a conversation is open; Approve/Reject create an `inbox_decision` proposal and render `ProposalCard` instead of `window.confirm`; Edit unchanged), `components/console/HrWorkspace.tsx` (publish `{tab:"hr", section, record:{kind:"training_request", id}}`; "Continue with submit" creates a `training_submit` proposal and renders the card; approve/reject unchanged), `components/console/EmailWorkspace.tsx` (publish `{tab:"email", record:{kind:"draft", id}}`; "Save to my Gmail drafts" creates an `email_export` proposal and renders the card), `components/console/BusinessWorkspace.tsx` and `KnowledgeWorkspace.tsx` (publish `{tab}` only), `app/(admin)/services/page.tsx`/portal (publish `{tab:"services"}`)
- Test: `check:isolated`; `tests/test_packaged_console.py -m slow` (the HR and inbox browser acceptances exercise these buttons; update their steps to confirm through the card: `proposal-confirm`)

- [ ] **Step 1–3:** implement; run `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_packaged_console.py -m slow` → 6 passed; `check:isolated`; commit `feat(console-web): tab actions confirm through shared proposals`.

---

### Task 12: Browser acceptance

**Files:**
- Create: `tests/test_workspace_browser.py` (harness copied from `tests/test_services_browser.py`: loopback stub upstream for `/auth/me` and `/services/active`, `page.route` stubs for browser calls)

**Scenario (one test):** tenant_admin with `customer_support`, `hr`, `staff_email` active; open the Inbox → dock button visible; open the dock, type "email Ali about the roster" → stubbed turn returns `DirectoryResult` with two Alis → pick one → stubbed turn returns `ProposalResult(email_export)` → card shows recipient and subject → Confirm posts `/workspace/proposals/<id>/confirm` with CSRF → result shown; type "submit my training request" → stubbed `NavigateResult(tab="hr", resume=true)` → URL becomes `/portal/hr` and the message is re-posted with `page_context.tab == "hr"` (assert the second turn body) → stubbed `ProposalResult(training_submit)` → Confirm; navigate away while a card is `requested` → `cancel` was called; `read_only` me → no dock button; platform_admin on `/inbox` without `client_id` → no dock button, with `client_id` → button.

- [ ] **Step 1–3:** write, run (`NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_workspace_browser.py` → 1 passed), commit `test(console): browser acceptance for the workspace assistant`.

---

### Task 13: Documentation

**Files:** `docs/CONSOLE_API.md` (replace "Authenticated internal workspace (A1)" with "Workspace assistant": routes, proposal lifecycle, gates, people, compose), `docs/ARCHITECTURE.md` (visibility paragraph), `docs/PROJECT_OVERVIEW.md` (schema `0460`, assistant line), `docs/EMAIL_ASSISTANT.md` (compose), `docs/PROGRESS.md` (entry with evidence; "Final verification: pending Task 14" marker), `.env.example` (`WORKSPACE_ASSISTANT_ENABLED=false`).

- [ ] **Step 1–3:** write; run the repository link checker (`0 broken` outside ui-guide/); `git diff --check`; commit `docs: describe the workspace assistant, proposals and company directory`.

---

### Task 14: Final verification

- [ ] `PATH=/tmp/fourstacks-toolbin:$PATH NO_LOCAL_ENV=1 ENV=ci make lint test` → 0 failures; `NO_LOCAL_ENV=1 ENV=ci uv run pytest -q tests/test_packaged_console.py -m slow` → 6 passed; `npm --prefix CSA_WebConsole/csa-console/web run check:isolated` → pass; `tests/test_migrate.py::test_make_reset_only_disposable_container` → pass.
- [ ] Replace the PROGRESS marker with the counts; commit `docs(progress): record workspace assistant validation`.
