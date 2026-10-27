# B1 Subscriptions Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tenant staff manage plans and their customers' subscriptions from a Subscriptions tab: search, see a timeline, act (pause, resume, change plan, cancel, extend renewal) through confirm cards, and decide the requests the customer-facing agent records. Due changes apply themselves on schedule. No money moves.

**Architecture:** A new package `csa/subscriptions/` (contracts, service, API, executor) on the existing `plans`, `subscriptions` and `subscription_events` tables plus one new decisions table. Every mutation that changes a subscription's state is a `subscription_action` proposal executed through the shared `ProposalRegistry`, so the card, claim lease, idempotency and audit already exist. A worker job applies flags that fall due. The frontend adds `SubscriptionsWorkspace` and flips the navigation key reserved in W0.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async, Pydantic; Next.js/TypeScript; Playwright.

**Spec:** `docs/superpowers/specs/2026-10-27-client-growth-program-design.md` (section 7, B1; owner price rule and designer's calls in section 12).

## Global Constraints

- Migration range for this lane: `0480`–`0489`. This plan ships `0480_subscriptions_console.sql` and raises `EXPECTED_SCHEMA_VERSION` to the highest merged version (one line, per W0).
- Statuses: `subscriptions.status ∈ {active, paused, cancelled}`. `cancel_at_period_end`, `pending_plan`/`pending_effective_at` and `resume_at` are flags, not statuses.
- Actions: `pause`, `resume`, `change_plan`, `cancel_at_period_end`, `cancel_now`, `extend_renewal`. Every executed action inserts one `subscription_events` row with `actor = <user id as text>` (or `system:subscriptions_due`), `action`, `from_status`, `to_status`, `payload` (JSON with the fields below), and increments `subscriptions.version`.
- Price rule (owner decision): `change_plan` sets `price` to the new plan's current price at the effective date and records `{"old_price","new_price","old_plan","new_plan"}` in the event payload; editing a plan's price never changes existing subscriptions; staff may pass `price_override` only with a non-empty `reason`.
- Request queue: `agent_preparation_requests` (`agent='subscription_agent'`) joined to the new `subscription_request_decisions`; the legacy `subscription_requests` table is not used. Lane B reads `csa/agents/staged/` and does not change it.
- Roles: every member (including `read_only`) may read; `tenant_admin` and `tenant_staff` may act; platform admins act for the tenant named by `?client_id=`. Package gate `package_gate("subscriptions")` (`active` only).
- Money is displayed as "RM 59.90"; dates as "27 Oct 2026"; copy follows `docs/WRITING_GUIDE.md`.
- `NO_LOCAL_ENV=1 ENV=ci make lint test`; disposable Testcontainers only; full suite on CI; Playwright needs `export PATH=/tmp/fourstacks-toolbin:$PATH`.
- Lane B owns `csa/subscriptions/`, `csa/workspace/contracts.py` (`ProposalKind` literal), `csa/workspace/executors.py` (registry builder line), `csa/jobs/subscriptions.py`, `db/sql/subscriptions/`, `db/sql/jobs/subscriptions_due.sql`, `components/console/SubscriptionsWorkspace.tsx`, `lib/subscriptions-api.ts`, `lib/workspace-api.ts` (`ProposalKind`), `lib/navigation.ts`, the two page files. Do not touch `csa/channels/`, `apps/delivery/`, `csa/console/inbox.py`, `csa/console/packages.py`.
- Branch `feat/subscriptions-tab` from `main` after W0 merges, in a worktree under `.worktrees/`.

## Review Focus

1. Two staff confirm the same `pause` card concurrently: exactly one event row, one version bump (Task 3 test; the registry's claim lease is relied on, the test proves it for this executor).
2. A proposal created with `expected_version = 3` confirmed after someone else changed the subscription: `proposal_stale`, no change (Task 3 test).
3. `change_plan` to an archived plan or a plan of another tenant: card creation refuses with 422 `plan_unavailable`, nothing stored (Task 3 test).
4. Due job at exactly `renews_at`: `cancel_at_period_end` wins over renewal. A `pending_plan` whose plan was archived in the meantime is not applied: the job records `pending_change_skipped` and clears the pending fields (Task 5 test).
5. A `read_only` member calling an action route: 403 before any proposal row is written (Task 3 test).

---

### Task 1: Migration 0480 and schema contract

**Files:**
- Create: `db/migrations/0480_subscriptions_console.sql`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION`; set to `"0480"`, or keep the higher value if `0470` has merged and is lower, see W0 rule)
- Test: `tests/test_subscriptions_schema.py`

**Interfaces:**
- Migration (additive; header with pre-check "0460 applied; validates existing rows before adding CHECKs"; rollback note "drop the new table and constraints; data columns stay"):
  - `ALTER TABLE public.subscriptions ADD COLUMN version integer NOT NULL DEFAULT 1;`
  - `ALTER TABLE public.subscriptions ADD CONSTRAINT subscriptions_status_chk CHECK (status IN ('active','paused','cancelled')) NOT VALID; ALTER TABLE public.subscriptions VALIDATE CONSTRAINT subscriptions_status_chk;` (the migration first `UPDATE`s any legacy status outside the set to `cancelled` and records that count in a `RAISE NOTICE`).
  - `ALTER TABLE public.plans ADD CONSTRAINT plans_billing_interval_chk CHECK (billing_interval IN ('month','year')) NOT VALID; VALIDATE ...` after normalising legacy values.
  - `ALTER TABLE public.subscription_events ADD COLUMN proposal_id uuid; CREATE UNIQUE INDEX subscription_events_proposal_key ON public.subscription_events (proposal_id) WHERE proposal_id IS NOT NULL;`
  - `CREATE TABLE public.subscription_request_decisions (client_id uuid NOT NULL REFERENCES public.clients(id), request_id uuid PRIMARY KEY REFERENCES public.agent_preparation_requests(id), decision text NOT NULL CHECK (decision IN ('approved','declined')), note text CHECK (note IS NULL OR length(note) <= 1000), decided_by uuid NOT NULL REFERENCES public.console_users(id), proposal_id uuid REFERENCES public.console_proposals(id), decided_at timestamptz NOT NULL DEFAULT now());` with `ENABLE ROW LEVEL SECURITY`, policy `tenant_isolation` identical to `0110`, `GRANT SELECT, INSERT ON ... TO csa_app; GRANT SELECT TO csa_ro;` (no UPDATE/DELETE: decisions are final).
  - `ALTER TABLE public.console_proposals DROP CONSTRAINT console_proposals_kind_check; ADD CONSTRAINT console_proposals_kind_check CHECK (kind IN ('email_export','training_submit','inbox_decision','subscription_action'));` (find the constraint's actual name in `0460` and use it).
  - Indexes: `subscriptions (client_id, status)`, `subscriptions (client_id, renews_at) WHERE status = 'active'`, `subscriptions (client_id, pending_effective_at) WHERE pending_plan IS NOT NULL`, `subscriptions (client_id, resume_at) WHERE status = 'paused'`, `agent_preparation_requests (client_id, agent, created_at DESC)`.
  - Grants check: `csa_app` already has DML on `plans`, `subscriptions`, `subscription_events` via `0100`; the migration asserts it with a `DO` block that raises if `has_table_privilege('csa_app','public.subscriptions','UPDATE')` is false.

- [ ] **Step 1: Write the failing tests** (`tests/test_subscriptions_schema.py`, `pytest_plugins = ["tests.test_console_sessions"]`, use `sessions.q`)

```python
async def test_0480_constraints_table_grants_and_rls(sessions):  # status CHECK rejects 'trialing'; billing_interval CHECK; decisions table exists with RLS enabled and tenant_isolation policy; csa_app has SELECT, INSERT and not UPDATE on decisions; console_proposals accepts kind 'subscription_action'
async def test_subscription_events_proposal_id_is_unique_when_present(sessions):  # two inserts same proposal_id → UniqueViolation; two NULLs fine
async def test_rls_blocks_cross_tenant_subscription_reads_for_csa_app(sessions):  # using the app pool with app.client_id = tenant A, rows of tenant B invisible
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_subscriptions_schema.py -q`
Expected: failures on missing table/constraints.

- [ ] **Step 3: Write the migration; bump the expected version**

- [ ] **Step 4: Run schema, migrate, health and the staged-agent tests**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_subscriptions_schema.py tests/test_migrate.py tests/test_health.py tests/test_workspace_registry.py -q -k "schema or migrate or shipped or proposals_table"`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add db/migrations/0480_subscriptions_console.sql apps/gateway/main.py tests/test_subscriptions_schema.py
git commit -m "feat(subscriptions): migration 0480 with status checks, decisions table and proposal kind"
```

---

### Task 2: Contracts, service and read/plan routes

**Files:**
- Create: `csa/subscriptions/__init__.py`, `csa/subscriptions/contracts.py`, `csa/subscriptions/service.py`, `csa/subscriptions/api.py`
- Create: `db/sql/subscriptions/plans_list.sql`, `plan_get.sql`, `plan_insert.sql`, `plan_update.sql`, `subscriptions_search.sql`, `subscription_get.sql`, `subscription_events.sql`, `subscription_insert.sql`, `event_insert.sql`
- Modify: `apps/gateway/main.py` (lane B line: `app.include_router(build_subscriptions_router())`)
- Test: `tests/test_console_subscriptions.py`

**Interfaces:**
- Contracts (`csa/subscriptions/contracts.py`, all `Contract` subclasses with `extra="forbid"`):

```python
Status = Literal["active", "paused", "cancelled"]
Interval = Literal["month", "year"]


class PlanOut(Contract):
    code: str
    name: str
    price: Decimal
    currency: str
    billing_interval: Interval
    features: list[str]
    sort_order: int
    is_active: bool


class PlanIn(Contract):
    code: str = Field(pattern=r"^[a-z0-9_-]{1,64}$")
    name: str = Field(min_length=1, max_length=120)
    price: Decimal = Field(ge=0, max_digits=12, decimal_places=2)
    currency: str = "MYR"
    billing_interval: Interval = "month"
    features: list[str] = Field(default_factory=list, max_length=20)
    sort_order: int = 0


class PlanPatch(Contract):
    name: str | None = None
    price: Decimal | None = None
    billing_interval: Interval | None = None
    features: list[str] | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class CustomerRef(Contract):
    id: UUID
    full_name: str | None
    phone: str | None
    email: str | None


class SubscriptionOut(Contract):
    id: UUID
    customer: CustomerRef
    plan: str
    plan_name: str | None
    status: Status
    price: Decimal
    currency: str
    started_at: datetime
    renews_at: datetime | None
    paused_at: datetime | None
    cancelled_at: datetime | None
    resume_at: date | None
    pending_plan: str | None
    pending_effective_at: datetime | None
    cancel_at_period_end: bool
    version: int


class SubscriptionPage(Contract):
    items: list[SubscriptionOut]
    next_cursor: UUID | None


class EventOut(Contract):
    id: int
    actor: str
    action: str
    from_status: str | None
    to_status: str | None
    payload: dict[str, Any]
    created_at: datetime


class SubscriptionDetail(Contract):
    subscription: SubscriptionOut
    events: list[EventOut]


class SubscriptionCreateIn(Contract):
    contact_id: UUID
    plan_code: str
    started_at: datetime | None = None
    renews_at: datetime | None = None
```

- Service (`csa/subscriptions/service.py`), every function takes `conn` inside the caller's `tx(pool, client_id)`:

```python
async def list_plans(conn, client_id: UUID, *, include_archived: bool) -> list[PlanOut]
async def create_plan(conn, client_id: UUID, actor: ConsoleActor, data: PlanIn) -> PlanOut        # 409 plan_exists
async def update_plan(conn, client_id: UUID, actor: ConsoleActor, code: str, data: PlanPatch) -> PlanOut   # 404 plan_not_found
async def search_subscriptions(conn, client_id: UUID, *, q: str | None, status: Status | None, plan: str | None, before: UUID | None, limit: int) -> SubscriptionPage   # q matches contacts.full_name ILIKE, phone digits, email ILIKE
async def get_subscription(conn, client_id: UUID, subscription_id: UUID) -> SubscriptionDetail  # 404 subscription_not_found; last 100 events newest first
async def create_subscription(conn, client_id: UUID, actor: ConsoleActor, data: SubscriptionCreateIn) -> SubscriptionOut  # plan must be active (422 plan_unavailable), contact must exist in tenant (404 contact_not_found); started_at default now; renews_at default started_at + interval; price snapshot from plan; event 'created'
```

- Routes (`csa/subscriptions/api.py`, `build_subscriptions_router() -> APIRouter`, prefix `/console/v1/subscriptions`, dependencies `[Depends(package_gate("subscriptions"))]`, tenant via `Depends(tenant)`; role helper `require_subscriptions_write(actor, client_id)` mirroring `require_business_read` but refusing `read_only`; reads allow every member of the tenant and platform admins):
  - `GET /plans?include_archived=false`, `POST /plans` (201), `PATCH /plans/{code}`
  - `GET /?q=&status=&plan=&before=&limit=50`, `GET /{id}`, `POST /` (201)
  - Audit each write with `csa.console.kb_uploads.audit` (the helper the inbox uses) as `subscriptions.plan.created|updated`, `subscriptions.created`.

- [ ] **Step 1: Write the failing tests** (`tests/test_console_subscriptions.py`; `pytest_plugins = ["tests.test_console_sessions"]`; autouse fixture `activate(sessions, tenant, "subscriptions")` for both tenants; helper `seed_customer(w, tenant) -> contact_id` inserting a `contacts` row with phone and email; helper `seed_plan(w, tenant, code="basic", price="29.90")`)

```python
async def test_plans_crud_roles_and_isolation(sessions)  # admin creates 201, duplicate 409, staff PATCH price → 200, reader POST → 403, archived plan hidden unless include_archived, other tenant sees nothing
async def test_create_subscription_snapshots_price_and_records_event(sessions)  # price '29.90', renews_at = started_at + 1 month, event 'created' actor = user id
async def test_create_refuses_archived_plan_and_foreign_contact(sessions)  # 422 plan_unavailable; 404 contact_not_found for a contact of tenant B
async def test_search_by_name_phone_digits_email_status_plan_with_cursor(sessions)  # q='0123' matches '+60 12-345 6789'; limit=1 paginates
async def test_detail_lists_events_newest_first(sessions)
async def test_package_gate_and_cross_tenant_query(sessions)  # tenant without package → 403 package_inactive; ?client_id=other as tenant admin → 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_subscriptions.py -q`
Expected: 404 on every route.

- [ ] **Step 3: Implement contracts, SQL, service and routes; register on the lane B line**

- [ ] **Step 4: Regenerate the OpenAPI snapshot and run**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run python -m scripts.export_console_openapi && NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_console_subscriptions.py tests/test_console_contract_snapshot.py -q`
Expected: pass.

- [ ] **Step 5: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/subscriptions db/sql/subscriptions apps/gateway/main.py docs/console_openapi.json tests/test_console_subscriptions.py
git commit -m "feat(subscriptions): plans and subscription reads, creation and search"
```

---

### Task 3: `subscription_action` executor and the actions route

**Files:**
- Create: `csa/subscriptions/executor.py`
- Create: `db/sql/subscriptions/subscription_lock.sql`, `subscription_apply.sql`, `event_by_proposal.sql`
- Modify: `csa/workspace/contracts.py:192` (`ProposalKind` gains `"subscription_action"`), `csa/workspace/executors.py::build_proposal_registry` (register `SubscriptionActionExecutor()`), `csa/subscriptions/api.py` (`POST /{id}/actions`), `csa/subscriptions/contracts.py` (`ActionIn`)
- Modify: `CSA_WebConsole/csa-console/web/lib/workspace-api.ts:44` (`ProposalKind` union)
- Test: `tests/test_subscriptions_executor.py`

**Interfaces:**

```python
Action = Literal["pause", "resume", "change_plan", "cancel_at_period_end", "cancel_now", "extend_renewal"]
class ActionIn(Contract):
    action: Action
    resume_at: date | None = None            # pause only (optional)
    plan_code: str | None = None             # change_plan (required)
    effective_at: datetime | None = None     # change_plan: default now → immediate; future → pending_plan
    renews_at: datetime | None = None        # extend_renewal (required, must be after current renews_at)
    price_override: Decimal | None = None    # change_plan only, requires reason
    reason: str | None = Field(default=None, max_length=500)
    # model_validator enforces the per-action requirements above with messages that name the field

class SubscriptionActionExecutor:
    kind: ProposalKind = "subscription_action"
    async def gate(...)    # require_package(pool, client_id, {"subscriptions"}, ACTIVE_ONLY) for both origins; there is no per-tenant assistant switch for this kind, so origin does not change the gate (say so in the docstring).
    async def card(...)    # validate payload {"subscription_id": UUID, **ActionIn}; load the subscription as the user (404 → ProposalError? no: raise HTTPException(404,"subscription_not_found")); check the transition is legal for the current status (422 action_not_allowed), the target plan is active in this tenant (422 plan_unavailable); CardContent(title="Pause subscription", summary="<customer> · <plan> · RM 29.90 a month", fields=[("Customer", name), ("Plan", ...), ("Change", human sentence), ("Reason", reason or "—")]); version = str(subscription.version)
    async def execute(...) # replay: if event_by_proposal(proposal_id) exists → return its stored result; else lock the row FOR UPDATE, compare version with expected_version (mismatch → ProposalError("proposal_stale")), apply, insert event with proposal_id, return {"subscription_id", "status", "version", "event_id"}
```

Transition table (card refuses anything else):

| action | from | effect |
|---|---|---|
| pause | active | status paused, paused_at now, resume_at as given |
| resume | paused | status active, paused_at null, resume_at null; if renews_at < now set renews_at = now + interval |
| change_plan | active, paused | effective_at ≤ now: plan, price (override or plan price) now; else pending_plan + pending_effective_at |
| cancel_at_period_end | active | cancel_at_period_end true |
| cancel_now | active, paused | status cancelled, cancelled_at now, cancel_at_period_end false, pending cleared |
| extend_renewal | active | renews_at = given (must be later than current) |

Route: `POST /console/v1/subscriptions/{id}/actions` body `ActionIn`, write role required, returns `ProposalOut` (201) from `registry.create(pool, request, actor, client_id, "subscription_action", {"subscription_id": str(id), **body}, thread_id=None, origin="tab")`; the registry is `request.app.state.proposal_registry`.

- [ ] **Step 1: Write the failing tests** (`tests/test_subscriptions_executor.py`; reuse `sessions`, `activate`, seeds from Task 2's test module via import; confirm cards through `POST /console/v1/workspace/proposals/{id}/confirm` as the same browser)

```python
@pytest.mark.parametrize("action,from_status,to_status", [("pause","active","paused"),("resume","paused","active"),("cancel_now","active","cancelled"),("cancel_at_period_end","active","active")])
async def test_action_card_then_confirm_applies_once_with_event(sessions, action, from_status, to_status)  # ProposalOut requested → confirm executed; subscription status; one event with proposal_id, actor = user id; version +1; replay confirm returns same result without a second event
async def test_change_plan_immediate_uses_new_plan_price_and_records_both(sessions)  # old 29.90 → new 59.90; payload old_price/new_price
async def test_change_plan_future_sets_pending_fields_only(sessions)
async def test_price_override_requires_reason(sessions)  # 422 from ActionIn validation
async def test_illegal_transition_and_unavailable_plan_are_refused_at_card(sessions)  # resume on active → 422 action_not_allowed; archived plan → 422 plan_unavailable; no proposal row
async def test_stale_version_is_refused(sessions)  # create card, bump version directly via owner SQL, confirm → 409 proposal_stale; no event
async def test_concurrent_confirms_execute_once(sessions)  # asyncio.gather two confirms → one event row
async def test_read_only_cannot_create_action_and_reader_sees_no_proposals(sessions)  # 403, no console_proposals row
async def test_extend_renewal_must_move_forward(sessions)
```

- [ ] **Step 2: Run to verify they fail**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_subscriptions_executor.py -q`
Expected: 404 / unknown kind.

- [ ] **Step 3: Implement the executor, register it, extend both `ProposalKind` literals, add the route**

- [ ] **Step 4: Run with the registry suite**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run python -m scripts.export_console_openapi && NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_subscriptions_executor.py tests/test_workspace_registry.py tests/test_workspace_contracts.py tests/test_console_contract_snapshot.py -q`
Expected: pass.

- [ ] **Step 5: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/subscriptions csa/workspace/contracts.py csa/workspace/executors.py db/sql/subscriptions docs/console_openapi.json CSA_WebConsole/csa-console/web/lib/workspace-api.ts tests/test_subscriptions_executor.py
git commit -m "feat(subscriptions): subscription_action proposals executed through the registry"
```

---

### Task 4: Request queue and decisions

**Files:**
- Modify: `csa/subscriptions/contracts.py` (`RequestOut`, `RequestPage`, `DecideIn`), `csa/subscriptions/service.py`, `csa/subscriptions/api.py`
- Create: `db/sql/subscriptions/requests_list.sql`, `request_get.sql`, `decision_insert.sql`
- Test: `tests/test_subscriptions_requests.py`

**Interfaces:**

```python
class RequestOut(Contract):
    id: UUID
    subscription_id: UUID
    customer: CustomerRef
    action: Literal["request_pause", "request_cancel", "request_change"]
    plan_code: str | None
    reason: str | None
    requested_at: datetime
    decision: Literal["approved", "declined"] | None
    decided_at: datetime | None
    decided_by: UUID | None
    note: str | None
    proposal_id: UUID | None


class RequestPage(Contract):
    items: list[RequestOut]
    next_cursor: UUID | None


class DecideIn(Contract):
    decision: Literal["approve", "decline"]
    note: str | None = Field(default=None, max_length=1000)
    resume_at: date | None = None  # pause approvals may set a resume date
```

- `GET /requests?status=open|decided&before=&limit=50` — `open` = no decision row; rows come from `agent_preparation_requests` where `agent='subscription_agent'` and `action IN ('request_pause','request_cancel','request_change')`, payload fields `subscription_id`, `plan_code`, `reason`.
- `POST /requests/{id}/decide` (write role): `decline` → insert decision row, audit `subscriptions.request.declined`, return `RequestOut`. `approve` → map to an action (`request_pause` → `pause` with `resume_at`; `request_cancel` → `cancel_at_period_end`; `request_change` → `change_plan` immediate with the requested `plan_code`), `registry.create(... origin="tab")` then `registry.confirm(...)` in the same request handler, insert the decision row with `proposal_id`, audit `subscriptions.request.approved`, return `RequestOut` with `decision="approved"`. If the card refuses (illegal transition, archived plan) the handler returns that 422 and writes no decision row. Deciding twice → 409 `request_decided`.

- [ ] **Step 1: Write the failing tests** (`tests/test_subscriptions_requests.py`; seed requests by inserting `agent_preparation_requests` rows directly with `sessions.q`, matching the shape `SubscriptionAgent` writes: payload `{"action":"request_pause","subscription_id":...,"reason":"holiday"}`)

```python
async def test_open_queue_lists_only_undecided_requests_with_customer(sessions)
async def test_approve_pause_creates_and_confirms_proposal_and_records_decision(sessions)  # subscription paused; decision row with proposal_id; events 1; audit approved
async def test_approve_change_to_archived_plan_is_refused_without_decision(sessions)  # 422, no decision row, subscription unchanged
async def test_decline_records_note_and_leaves_subscription(sessions)
async def test_decide_twice_is_409_and_reader_is_403(sessions)
async def test_requests_are_tenant_scoped(sessions)
```

- [ ] **Step 2: Run to verify they fail**, implement, then run:

Run: `NO_LOCAL_ENV=1 ENV=ci uv run python -m scripts.export_console_openapi && NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_subscriptions_requests.py tests/test_console_subscriptions.py tests/test_console_contract_snapshot.py -q`
Expected: pass.

- [ ] **Step 3: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/subscriptions db/sql/subscriptions docs/console_openapi.json tests/test_subscriptions_requests.py
git commit -m "feat(subscriptions): request queue with approve-and-confirm and decline"
```

---

### Task 5: Due-changes worker job

**Files:**
- Create: `csa/jobs/subscriptions.py`, `db/sql/jobs/subscriptions_due.sql`
- Modify: `csa/jobs/runner.py::build_default_registry` (register `Job(name="subscriptions_due", every=timedelta(minutes=5), fn=run_subscriptions_due, per_client=True)`)
- Test: `tests/test_jobs_subscriptions.py`

**Interfaces:**

```python
async def run_subscriptions_due(conn: psycopg.AsyncConnection[Any], client_id: UUID, clock: Clock, *, batch_size: int = 100) -> dict[str, Any]
# returns {"cancelled": n, "plan_changed": n, "resumed": n, "renewed": n, "pending_skipped": n}
```

Order of evaluation per batch (one SQL per rule, each `FOR UPDATE SKIP LOCKED`, `LIMIT batch_size`, `now = clock.now()`), each applying the change, bumping `version`, and inserting a `subscription_events` row with `actor = 'system:subscriptions_due'`:
1. `status='active' AND cancel_at_period_end AND renews_at <= now` → `cancelled` (`cancelled_at = renews_at`), action `cancelled_at_period_end`.
2. `pending_plan IS NOT NULL AND pending_effective_at <= now`: if the plan is active → plan, price = plan price, clear pending, action `plan_changed` with old/new price; else clear pending, action `pending_change_skipped` with `{"plan": pending_plan}`.
3. `status='paused' AND resume_at <= now::date` → `active`, clear `paused_at`/`resume_at`, action `resumed`; if `renews_at < now` set `renews_at = now + interval`.
4. `status='active' AND NOT cancel_at_period_end AND renews_at <= now` → `renews_at = renews_at + interval` (month or year by plan `billing_interval`, default month when the plan is gone), action `renewed`. Nothing is charged.

- [ ] **Step 1: Write the failing tests** (`tests/test_jobs_subscriptions.py`, modelled on `tests/test_jobs.py::test_mute_expiry_batch_isolation_locks_and_idempotency`: owner connection, two tenants, `FakeClock`)

```python
async def test_cancel_at_period_end_wins_over_renewal_at_the_same_instant(...)
async def test_pending_plan_applies_at_plan_price_or_is_skipped_when_archived(...)
async def test_resume_date_reactivates_and_repairs_past_renewal(...)
async def test_renewal_advances_by_interval_month_and_year(...)  # 31 Jan + 1 month → 28/29 Feb (Postgres interval arithmetic), 1 year
async def test_batch_size_isolation_and_idempotency(...)  # batch_size=1 processes one; rerun processes the rest; third run zero; other tenant untouched
async def test_registry_contains_subscriptions_due_every_five_minutes(...)
```

- [ ] **Step 2: Run to verify they fail**, implement, then run:

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_jobs_subscriptions.py tests/test_jobs.py -q`
Expected: pass.

- [ ] **Step 3: Lint and commit**

```bash
NO_LOCAL_ENV=1 ENV=ci make lint
git add csa/jobs/subscriptions.py csa/jobs/runner.py db/sql/jobs/subscriptions_due.sql tests/test_jobs_subscriptions.py
git commit -m "feat(subscriptions): worker job applies due cancellations, plan changes, resumes and renewals"
```

---

### Task 6: Subscriptions tab in the console

**Files:**
- Create: `CSA_WebConsole/csa-console/web/lib/subscriptions-api.ts`, `components/console/SubscriptionsWorkspace.tsx`, `app/portal/subscriptions/page.tsx`, `app/(admin)/subscriptions/page.tsx`
- Modify: `lib/navigation.ts` (add the entry `{ key: "subscriptions", label: "Subscriptions", href: to("subscriptions") }` after Business), `tests/nav-visibility.test.mjs` (replace the W0 reservation test with: member with `subscriptions` active sees "Subscriptions" at `/portal/subscriptions`; without it, not; platform admin sees it at `/subscriptions`)
- Test: `tests/subscriptions-api.test.mjs`, `tests/test_subscriptions_browser.py`

**Interfaces:**
- `subscriptions-api.ts`: typed mirrors of the contracts (`PlanOut`, `SubscriptionOut`, `SubscriptionDetail`, `RequestOut`, `ActionIn`) and functions `listPlans(tenantId, includeArchived)`, `createPlan`, `updatePlan`, `searchSubscriptions(tenantId, {q,status,plan,before})`, `getSubscription`, `createSubscription`, `proposeAction(tenantId, id, body): Promise<ProposalOut>`, `listRequests(tenantId, status, before)`, `decideRequest(tenantId, id, body)`; all through `api()` with the CSRF header on writes; `ApiError` plain-word map gains `plan_unavailable` ("That plan is not available for this company."), `action_not_allowed` ("This change is not possible from the subscription's current state."), `request_decided` ("Someone has already decided this request.").
- `SubscriptionsWorkspace({ me })`: three tabs "Customers", "Plans", "Requests". Customers: search box (placeholder "Search by name, phone or email"), status and plan selects, table (Customer, Plan, Status, Renews on, Price), row click opens a drawer: details, timeline (newest first, plain sentences built from action + payload), action buttons by current status (Pause, Resume, Change plan, Cancel at period end, Cancel now, Extend renewal); each opens a small form then calls `proposeAction` and renders `ProposalCard` with `tenantId`; on settled, refetch. Plans: table + "Add plan" form + inline edit + "Archive". Requests: open list with Approve/Decline buttons (approve for a pause asks for an optional resume date), decided list below. `read_only` sees everything with action controls hidden. Platform admins must have `?client_id=` (same rule as the dock; reuse how `BusinessWorkspace` resolves the tenant). Uses `Heading`, `Panel`, `Notice`, `action`, `secondary`, `field` from `./ui`.

- [ ] **Step 1: Write the failing node tests** (`tests/subscriptions-api.test.mjs`): URL building with and without tenant query; `proposeAction` posts to `/console/v1/subscriptions/{id}/actions`; error map entries resolve.

- [ ] **Step 2: Implement the client, the workspace, the pages and the navigation entry; update `nav-visibility.test.mjs`**

- [ ] **Step 3: Run the isolated frontend check**

Run: `export PATH=/tmp/fourstacks-toolbin:$PATH; npm --prefix CSA_WebConsole/csa-console/web run check:isolated`
Expected: pass.

- [ ] **Step 4: Browser acceptance** (`tests/test_subscriptions_browser.py`, stubbed upstream as in `tests/test_services_browser.py` extended with `/console/v1/subscriptions*` and `/console/v1/workspace/proposals/*` handlers): as tenant staff with the package active, the Subscriptions link exists; search shows one customer; opening it and clicking "Pause" then confirming the card calls confirm and the row shows "Paused"; Requests tab approves one request; as read_only no action buttons render; without the package the link is absent.

Run: `export PATH=/tmp/fourstacks-toolbin:$PATH; NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_subscriptions_browser.py -q`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add CSA_WebConsole/csa-console/web/lib/subscriptions-api.ts CSA_WebConsole/csa-console/web/lib/navigation.ts CSA_WebConsole/csa-console/web/components/console/SubscriptionsWorkspace.tsx "CSA_WebConsole/csa-console/web/app/portal/subscriptions/page.tsx" "CSA_WebConsole/csa-console/web/app/(admin)/subscriptions/page.tsx" CSA_WebConsole/csa-console/web/tests/subscriptions-api.test.mjs CSA_WebConsole/csa-console/web/tests/nav-visibility.test.mjs tests/test_subscriptions_browser.py
git commit -m "feat(console): Subscriptions tab with confirm cards and the request queue"
```

---

### Task 7: Documentation and progress record

**Files:**
- Modify: `docs/CONSOLE_API.md` (new "## Subscriptions" section: routes, roles, statuses and flags, action table, price rule, request queue, due job, audit actions), `docs/ARCHITECTURE.md` (one paragraph: subscriptions module and job), `docs/PROJECT_OVERVIEW.md` (packages table row for `subscriptions` now reads "Subscriptions (plans, customers' subscriptions, request queue)"), `docs/PROGRESS.md` (lane entry)
- Note for B3: `csa/console/packages.py` summary text for `subscriptions` is stale ("records unexecuted subscription requests") and is updated in the wording pass, not here.

- [ ] **Step 1: Write the documentation**; validate with `git diff --check`.

- [ ] **Step 2: Final focused run and PROGRESS entry**

Run: `NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_subscriptions_schema.py tests/test_console_subscriptions.py tests/test_subscriptions_executor.py tests/test_subscriptions_requests.py tests/test_jobs_subscriptions.py tests/test_workspace_registry.py tests/test_console_contract_snapshot.py tests/test_migrate.py tests/test_health.py -q`
Record counts and time; state that the full suite runs on CI.

- [ ] **Step 3: Commit**

```bash
git add docs/CONSOLE_API.md docs/ARCHITECTURE.md docs/PROJECT_OVERVIEW.md docs/PROGRESS.md
git commit -m "docs(subscriptions): console contract, architecture note and progress record"
```

Then use superpowers:finishing-a-development-branch (push and open a pull request against `main`). Rollout: apply `0480` with the `migrate` one-shot, recreate gateway, worker and frontend; activate the `subscriptions` package for the client from the Services tab.
