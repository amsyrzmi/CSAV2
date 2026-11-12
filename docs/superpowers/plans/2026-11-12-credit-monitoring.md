# Credit Monitoring (slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Meter every model run into our own ledger, price it with the owner's markup table plus the prorated platform fee, and show each client a Credit tab (executions, per-agent, per-execution, two charts, bill so far), with Langfuse as an OTLP mirror.

**Architecture:** A `ContextVar` binds client, trace and job at each entry point; the two provider clients (`GroqClient`, `GeminiClient`) record token usage into `ai_usage_events` through a required `UsageMeter`. Cost is never stored: `csa/billing/` pure functions price tokens at read time from append-only, effective-dated price, FX, fee, markup and plan tables. A new `/console/v1/billing` router serves a client view (prices only) and a platform-admin view (tokens, raw cost, margin). The Langfuse sink moves to OTLP last.

**Tech Stack:** Python 3.12, FastAPI, psycopg 3 async, Pydantic, SQL in `db/sql/billing/`, Postgres migrations 0570–0572, Next.js 16 console (`CSA_WebConsole/csa-console/web`), `recharts` 3.10.1 (already pinned), `opentelemetry-exporter-otlp-proto-http` (new, pinned).

**Spec:** `docs/superpowers/specs/2026-11-10-credit-monitoring-design.md` (read it first; this plan only records decisions the spec leaves open or the code forced).

## Global Constraints

- Worktree `.claude/worktrees/feat-credit-monitoring`, branch `feat/credit-monitoring`, now fast-forwarded to origin/main `942e765`. **Do not touch main, do not push, do not open a PR.** The owner tests manually in this worktree first (Task 14).
- Spec says migrations 0550–0552; **main now holds 0550 and 0560, so this plan uses 0570 (ledger), 0571 (billing config), 0572 (plans)**. Add a `0570`–`0579` line to `db/migrations/README.md`. Re-check that range is free before Task 1.
- Spec says twelve `json_completion` call sites; **main now has fourteen**: `csa/decide/llm.py` ×6 (465, 477, 676, 825, 855, 875), `csa/workspace/planner.py:467`, `csa/hr/assistant.py:107`, `csa/training/summary.py:110`, `csa/email/service.py` ×4 (1164, 1621, 1676, 1716), `csa/workspace/email_batch.py:623` (draft review). `GroqClient(`/`GeminiClient(` are built in `apps/worker/main.py:722,731`, `apps/gateway/main.py:238,248,267,279,294`, `csa/evals/decisions.py:262`.
- Python conventions from `AGENTS.md`: psycopg 3 async only, Pydantic for contracts, SQL in `db/sql/` (parameterized), only `csa/graph/` imports LangGraph, additive ordered migrations with RLS and no `FORCE`, exact dependency pins plus `uv.lock`, no silent fallbacks. Synthetic data only; never a production DSN.
- `Decimal` everywhere in money paths; currency RM; display rounding RM0.01 for fee and AI charge, RM0.0001 for execution prices.
- Billing cycle: 5th 00:00 to next 5th 00:00, `Asia/Kuala_Lumpur`. Pilot fee RM99.99, collaborator RM119.99, terminated RM0, prorated by days (`fee × days_billed ÷ days_in_cycle`, rounded 0.01). Markups (percent), tier by executions in cycle (≤2,000 / 2,001–10,000 / >10,000), whole cycle priced at the final tier:
  pilot 75/70/67.5, 70/65/65, 65/65/65 and collaborator 85/80/77.5, 80/77.5/77.5, 77.5/77.5/77.5 (Individual/Curated/Complete).
- Test vectors (within RM0.01): pilot Individual, 10,000 executions, raw 167.37 → AI 284.52, bill 384.51; collaborator Complete, 20,000, raw 334.73 → AI 594.15, bill 714.14; pilot Individual, 1,000, raw 16.73 → AI 29.28, bill 129.27.
- A client-facing payload never contains raw price or cost, tokens, markup percentage, margin or AWS cost. Prompt and reply text never go to the ledger, Langfuse or logs.
- Running tests in this worktree: Bash refuses `VAR=value` prefixes and compound commands here. Use a scratchpad wrapper that exports `NO_LOCAL_ENV=1 ENV=ci`, then run `bash <wrapper> uv run pytest <files> -q` (one command per call). Per the CI-as-merge-gate rule, run only the changed tests plus `make lint` locally; Docker-backed schema tests need Docker. Detach any long run with `nohup setsid`.
- Every commit message ends with `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.

## Review Focus

Inputs and conditions the spec implies but no feature task names; each has a test in the owning task.

1. **Usage at the cycle boundary.** A call at 2026-09-04T16:00:00Z (= 5 Sep 00:00 KL) belongs to the new cycle; 15:59:59Z to the old. UTC dates must never decide the cycle. (Task 7, Task 8)
2. **Client with a plan and zero usage.** Bill is the fee only, charts show every day of the window with zero, allocation and per-agent maths never divide by zero. (Task 7, Task 8)
3. **Provider fallback inside one job.** Groq fails then Gemini answers: two ledger rows with different models, one trace, one execution. (Task 3, Task 8)
4. **Unpriced model.** A row whose model has no price at `occurred_at` is "pending", never priced at zero and never an error page; the admin sees the count, and back-dating the price (shadow rule) resolves it. (Task 5, Task 8, Task 10)
5. **Paging while usage arrives.** The execution list cursor is stable: new executions during paging neither duplicate nor skip rows. (Task 9)

---

### Task 1: Ledger table `ai_usage_events` (migration 0570)

**Files:**
- Create: `db/migrations/0570_ai_usage_events.sql`, `tests/test_ai_usage_schema.py`
- Modify: `db/migrations/README.md`, `apps/gateway/main.py:71` (`EXPECTED_SCHEMA_VERSION = "0570"`)

**Interfaces:**
- Produces: table `ai_usage_events(id bigint generated always as identity primary key, client_id uuid not null references clients(client_id), occurred_at timestamptz not null default now(), trace_id uuid not null, conversation_id uuid null, job text not null, provider text not null, model text not null, prompt_tokens int not null check (>= 0), completion_tokens int not null check (>= 0), schema_name text not null)`. `job` check is the closed set `customer_turn, workspace_assistant, hr_assistant, email_analysis, email_reply, email_compose, training_summary`. Indexes `(client_id, occurred_at)` and `(client_id, trace_id)`.

- [ ] **Step 1: Write the failing schema test** in `tests/test_ai_usage_schema.py`, modelled on `tests/test_escalation_note_schema.py` (`requires_docker`, `_owner`, `_app`, `_company` helpers). Tests: `test_table_columns_and_job_check` (insert with job `"nonsense"` raises `CheckViolation`; negative tokens raise), `test_rls_isolates_clients` (rows written as client A under `app.client_id` are invisible to client B through `csa_app`), `test_app_role_can_insert_and_select_but_not_update_or_delete`, `test_ro_role_selects`, `test_not_in_retention_purge_list` (the 30-day retention list in the baseline/retention code does not mention `ai_usage_events`; find the list with `grep -rn "agent_runs" csa/graph/retention.py db/`), `test_schema_version_is_0570`.
- [ ] **Step 2: Run** `bash <wrapper> uv run pytest tests/test_ai_usage_schema.py -q`. Expected: FAIL (no table).
- [ ] **Step 3: Write the migration** following the RLS pattern of a recent tenant table (read `0541_training_library.sql`: tenant-isolation policy on `app.client_id`, grants to `csa_app` insert+select and `csa_ro` select, no `FORCE`). Add the README line (`0570`–`0579`: credit monitoring) and bump `EXPECTED_SCHEMA_VERSION`.
- [ ] **Step 4: Run the test again** plus `tests/test_migrate.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(billing): ai_usage_events ledger table`.

---

### Task 2: `csa/metering/` package (context, meter, ledger writer)

**Files:**
- Create: `csa/metering/__init__.py`, `csa/metering/context.py`, `csa/metering/meter.py`, `db/sql/billing/usage_insert.sql`, `tests/test_metering.py`

**Interfaces:**
- Produces (`context.py`):
  `Job = Literal["customer_turn","workspace_assistant","hr_assistant","email_analysis","email_reply","email_compose","training_summary"]`; `JOB_LABELS: dict[Job, str]` ("Customer reply", "Assistant request", "HR assistant", "Email summary", "Email reply draft", "Email compose", "Training summary"); `JOB_AGENT: dict[Job, str]` (`support`, `assistant`, `hr`, `email`×3, `training`); `AGENT_NAMES` is NOT here (titles live in the web `AGENT_TITLES`; the API returns the title for `training` as "Training summary" and reads other titles from a Python map `AGENT_TITLES` in `csa/billing/agents.py`, Task 8, mirroring `lib/agent-avatars.ts`);
  `@dataclass(frozen=True) class MeterContext: client_id: UUID; trace_id: UUID; job: Job; conversation_id: UUID | None = None`;
  `def meter_context(ctx: MeterContext) -> ContextManager[None]` (sync `contextlib.contextmanager` over a `ContextVar`; resets the token on exit, nests correctly);
  `def current_context() -> MeterContext` (raises `MeteringContextMissing`); `class MeteringContextMissing(RuntimeError)`.
- Produces (`meter.py`):
  `@dataclass(frozen=True) class Usage: provider: str; model: str; prompt_tokens: int; completion_tokens: int; schema_name: str`;
  `class GenerationMirror(Protocol): def submit(self, ctx: MeterContext, usage: Usage) -> None` (non-blocking, never raises);
  `class UsageMeter(Protocol): def require_context(self) -> None; async def record(self, usage: Usage) -> None`;
  `class NullMeter` (no-op both; evals only);
  `class LedgerMeter: __init__(self, pool: AsyncConnectionPool, mirror: GenerationMirror | None = None)`; `require_context()` calls `current_context()`; `record()` reads the context, inserts one row with `tx(pool, str(ctx.client_id))` in its own transaction, retries once on `psycopg.Error`, on second failure logs `ai_usage_write_failed` at ERROR with client, trace, job, provider, model and token counts (no text) and returns normally; after the write attempt (success or not) calls `mirror.submit(ctx, usage)` if a mirror is set.

- [ ] **Step 1: Write failing tests** in `tests/test_metering.py`: `test_context_missing_raises`; `test_context_binds_and_nests_and_resets`; `test_job_maps_cover_closed_set` (`JOB_LABELS`, `JOB_AGENT` keys equal `get_args(Job)` and the SQL check list in 0570); `test_ledger_meter_writes_one_row` (Docker DB; row has client, trace, job, tokens); `test_ledger_failure_retries_once_then_logs_and_returns` (pool whose `connection()` raises `psycopg.OperationalError`; assert two attempts, an ERROR log event with the payload, no raise, mirror still called); `test_ledger_write_uses_its_own_transaction` (a caller's open transaction that rolls back does not remove the row); `test_mirror_exception_is_never_raised`.
- [ ] **Step 2: Run, expect FAIL** (module missing).
- [ ] **Step 3: Implement** the types and `LedgerMeter` above; the insert lives in `db/sql/billing/usage_insert.sql` and is run through the existing `csa.core.db` query helper used elsewhere (`q.execute("billing/usage_insert.sql", params, conn=conn)`).
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(metering): context, usage meter and ledger writer`.

---

### Task 3: Provider clients record usage; wiring; source-scan test

**Files:**
- Modify: `csa/decide/groq.py`, `csa/decide/gemini.py`, `apps/worker/main.py:~715-735`, `apps/gateway/main.py:~238-300`, `csa/evals/decisions.py:262`, `tests/test_groq_chat_url.py`, `tests/test_llm_provider.py`, `tests/test_hr_operations.py`, `tests/test_workspace_registry.py` (every place that builds a real client gets `meter=NullMeter()` or a recording fake)
- Create: `tests/test_metering_clients.py`, `tests/test_metering_source_scan.py`

**Interfaces:**
- Consumes: `UsageMeter`, `Usage`, `NullMeter` from Task 2.
- Produces: `GroqClient(api_key, model, http_client, url=GROQ_URL, *, meter: UsageMeter)` and `GeminiClient(api_key, model, http_client, *, meter: UsageMeter)`. Each `json_completion` calls `self._meter.require_context()` **before** any HTTP request (fail before spend), and `await self._meter.record(Usage(...))` after **every** HTTP response whose body carries usage, including attempts that are then retried and replies that fail schema validation, and never for responses without usage (timeouts, 5xx without a usage block). Groq reads `usage.prompt_tokens` / `usage.completion_tokens`. Gemini reads `usageMetadata.promptTokenCount` and `candidatesTokenCount + thoughtsTokenCount` (default 0 when `thoughtsTokenCount` is absent). **Builder: confirm both field sets against the providers' current docs and a recorded real response shape before relying on them.** Gemini's `provider` is `"gemini"`, Groq's `"groq"`; `schema_name` is `schema.__name__`.

- [ ] **Step 1: Failing tests** in `tests/test_metering_clients.py` using `httpx.MockTransport`: `test_groq_records_usage_on_success`; `test_groq_records_each_attempt_that_carries_usage` (first attempt a 429 whose body has a usage block, second a 200 with usage → two records; a first attempt 500 without usage then a 200 → one record); `test_groq_records_usage_when_output_fails_validation` (200 with usage and invalid JSON raises `groq_invalid_output` and still records); `test_groq_no_usage_block_records_nothing`; `test_gemini_records_usage_with_thinking_tokens` (completion = candidates + thoughts); `test_both_require_context_before_http` (no bound context → `MeteringContextMissing`, transport never called); `test_fallback_inside_one_job_records_two_rows_one_trace` (Groq then Gemini under one `meter_context`, using `LedgerMeter` with an in-memory fake pool or a recording `UsageMeter` double; two `Usage` records, same context).
- [ ] **Step 2: Failing source scan** in `tests/test_metering_source_scan.py` (style of `tests/test_bookings_bot_schema.py`): parse `apps/` and `csa/` with `ast`; every call to `GroqClient` or `GeminiClient` has a `meter` keyword, and only `csa/evals/` may pass `NullMeter`.
- [ ] **Step 3: Run, expect FAIL.**
- [ ] **Step 4: Implement** the client changes; wire `apps/worker/main.py` and `apps/gateway/main.py` to build one `LedgerMeter(pool, mirror=None)` each (the gateway's pool is `app.state` pool already in scope; worker uses `app_pool`) and pass it to every client; `csa/evals/decisions.py` passes `NullMeter()`. Update the existing tests that construct real clients.
- [ ] **Step 5: Run** the new tests and the four edited test files. Expected: PASS. `make lint` clean.
- [ ] **Step 6: Commit** `feat(metering): provider clients record token usage`.

> Tasks 3 and 4 must both be on the branch before any deploy: after Task 3 every call site raises `MeteringContextMissing` until Task 4 binds its context.

---

### Task 4: Bind context at every entry point

**Files:**
- Modify: `apps/worker/main.py` (customer turn, near `process_once` / the call into `csa/graph/coordinator.py` `process_turn`), `csa/workspace/service.py` (`create_planned_turn`, ~2607, and wherever the planner or email batch drafting runs), `csa/hr/assistant.py` (`HrAssistant.run`), `csa/training/service.py:~475`, `csa/email/service.py` (the four model calls at 1164, 1621, 1676, 1716)
- Create: `tests/test_metering_entry_points.py`

**Interfaces:**
- Consumes: `meter_context`, `MeterContext`, `Job`.
- Produces: each path runs its model calls inside `with meter_context(MeterContext(client_id, trace_id, job, conversation_id))`. Mapping: worker customer turn → `customer_turn` with the envelope's `trace_id` and conversation; `create_planned_turn` → `workspace_assistant`; `HrAssistant.run` → `hr_assistant`; training summary → `training_summary`; email thread analysis → `email_analysis`; reply draft → `email_reply`; batch compose (service.py 1676) → `email_compose`; `email_batch.review_draft` (email_batch.py:623) is called from the batch compose flow and **inherits the enclosing `email_compose` context** (assert this in a test, do not bind again). Gateway jobs mint `uuid4()` per request; the same request retried with the same idempotency key reuses the stored key-derived trace only where the code already persists an id (HR chat plans: derive the trace from the idempotency key with `uuid5`), otherwise a retry is a new execution.

- [ ] **Step 1: Failing tests** in `tests/test_metering_entry_points.py`, one per job: a fake `JsonCompletionClient` whose `json_completion` calls a real `LedgerMeter.record` (Docker DB) via a thin shim that mimics what the real clients do (`require_context` then `record(Usage(...))`); drive the entry point and assert one ledger row with the right `client_id`, `job` and a stable `trace_id`. Extra tests: `test_redelivered_turn_reuses_trace` (processing the same envelope twice yields rows with one distinct `trace_id`); `test_langgraph_nodes_see_bound_context` (a node running under the real compiled graph reads `current_context()`; if LangGraph runs nodes in tasks that do not inherit the context, bind inside the node wrapper in `csa/turn/deps.py` instead and keep this test); `test_batch_review_inherits_compose_job`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** the bindings; keep the `with` block as narrow as the model-calling code but wide enough to cover retries and fallbacks.
- [ ] **Step 4: Run** the new file and the existing tests of each touched module (`tests/test_email_*`, `tests/test_workspace_*`, `tests/test_hr_*`, `tests/test_training_*`, worker tests). Expected: PASS.
- [ ] **Step 5: Commit** `feat(metering): bind client, trace and job at each model entry point`.

---

### Task 5: Billing config tables (migration 0571) with shadow rule

**Files:**
- Create: `db/migrations/0571_billing_config.sql`, `db/sql/billing/prices_*.sql` (read in force, insert), `csa/billing/__init__.py`, `csa/billing/config.py`, `tests/test_billing_config_schema.py`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION = "0571"`), `db/migrations/README.md`

**Interfaces:**
- Produces tables per spec: `ai_model_prices(provider, model, input_usd_per_mtok numeric, output_usd_per_mtok numeric, effective_from timestamptz)`, `billing_fx_rates(usd_to_myr numeric, effective_from timestamptz)`, `billing_stage_fees(stage, monthly_fee_rm numeric, effective_from)`, `billing_markup_tiers(stage, bundle, up_to_executions int null, markup_pct numeric, effective_from)`. Append-only: `csa_app` gets select and insert only (no update/delete); a trigger or missing grants enforces it. Global (no client column). Fee and markup rows seeded from Global Constraints, effective `2020-01-01`. Seed prices for the models in settings (`GROQ_MODEL`, `ASSISTANT_DRAFT_MODEL`, `GOOGLE_AI_MODEL`: read `csa/core/` settings) with the source URL and date in a SQL comment; seed FX with the rate and its source in a comment. **Builder sources these values from the providers' public price pages and cites them; if a price cannot be sourced, leave the model unpriced (the unpriced flow then shows it) and say so in the commit.**
- `csa/billing/config.py`: `@dataclass(frozen=True) BillingConfig(prices: tuple[PriceRow,...], fx: tuple[FxRow,...], fees: tuple[FeeRow,...], tiers: tuple[TierRow,...])` plus `async def load_config(conn) -> BillingConfig` and pure lookups `price_at(config, provider, model, ts) -> PriceRow | None`, `fx_at(config, ts) -> Decimal`, `fee_for(config, stage, ts) -> Decimal`; and `def check_new_price_date(existing_dates: Sequence[datetime], effective_from: datetime, now: datetime) -> None` raising `ShadowRuleError` implementing the spec's shadow rule (back-dating allowed only before the earliest existing row; otherwise `effective_from` must be ≥ today and at most one day ahead; a date inside an existing priced range is refused).

- [ ] **Step 1: Failing tests** `tests/test_billing_config_schema.py` (Docker): seeds present with the exact fee and markup numbers; update and delete refused for `csa_app`; `price_at` picks the row in force at `occurred_at`; shadow-rule unit tests (back-date before earliest accepted, inside range refused, tomorrow accepted, three days ahead refused).
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** migration, SQL, `config.py`. **Step 4: Run, expect PASS.** **Step 5: Commit** `feat(billing): effective-dated price, FX, fee and markup tables`.

---

### Task 6: Plan history (migration 0572) and rule enforcement

**Files:**
- Create: `db/migrations/0572_client_billing_plans.sql`, `csa/billing/cycles.py`, `csa/billing/plans.py`, `db/sql/billing/plans_*.sql`, `tests/test_billing_cycles.py`, `tests/test_billing_plans.py`, `tests/test_billing_plans_schema.py`
- Modify: `apps/gateway/main.py` (`EXPECTED_SCHEMA_VERSION = "0572"`), `db/migrations/README.md`

**Interfaces:**
- Produces (`cycles.py`): `KL = ZoneInfo("Asia/Kuala_Lumpur")`; `@dataclass(frozen=True) Cycle: start: date; end: date` (end exclusive, always a 5th); `cycle_of(ts: datetime) -> Cycle` (KL local date decides; ts must be aware); `cycle_containing(d: date) -> Cycle`; `days_in(cycle) -> int`; `cycle_range(start: Cycle, end: Cycle) -> list[Cycle]`.
- Produces (`plans.py`): `Stage`, `Bundle` literals; `@dataclass(frozen=True) PlanRow: stage; bundle; effective_from: date; created_at: datetime`; `plan_for_cycle(history: Sequence[PlanRow], cycle: Cycle) -> PlanRow | None` (the latest row by `effective_from` then `created_at` among rows with `effective_from < cycle.end`, terminated rows included; `None` only before the first row); `billed_window(history, cycle) -> tuple[date, date] | None` (start = max(cycle.start, first-row date); end = termination date if it falls inside the cycle else cycle.end; `None` when not set up or fully terminated); `validate_new_plan(history, new: PlanRow, today: date) -> None` raising `PlanRuleError(code)` with codes `first_row_outside_current_cycle`, `change_not_on_a_fifth`, `before_current_cycle`, `after_termination`, `first_row_must_be_pilot_or_collaborator`, `bundle_change_same_stage_only`; `pilot_ended(history, today) -> bool` (three months from the first row's date, from the first cycle start on or after that point, stage still pilot).
  Note for `plan_for_cycle`: a `terminated` row yields stage `terminated` for the cycle containing or after its date; callers use `billed_window` to decide the fee and usage window.
- Table `client_billing_plans(id, client_id, stage, bundle, effective_from date, reason text, created_by text, created_at timestamptz default now())` with checks on `stage` and `bundle`, tenant RLS, `csa_app` select and insert only.
- Insert path (used by Task 10): `async def insert_plan(conn, actor_name, client_id, row, reason, today)` loads history, runs `validate_new_plan`, inserts, writes an audit row with before and after through the same audit helper `csa/console/hr_owner.py` uses for its platform-admin write (read `_send_owner_mail`/audit code there to find it).

- [ ] **Step 1: Failing tests.** `test_billing_cycles.py`: 5th boundary at 15:59:59Z vs 16:00:00Z (UTC) on 4 Sep 2026; Dec→Jan year rollover; cycle lengths 28, 30, 31 days (Feb 5→Mar 5 in 2027 is 28). `test_billing_plans.py`: one test per spec rule 1–7: first row is a join date inside the current cycle (join 15 Aug → window 15 Aug–5 Sep; earlier than cycle start refused), pilot→collaborator must be a 5th, same-date correction (newest wins, old row kept), scheduled future row applies only from its cycle, edit dated before the current cycle refused, termination mid-cycle gives window start→termination date, nothing after `terminated`, `pilot_ended` true from the first cycle start on or after three months. `test_billing_plans_schema.py` (Docker): check constraints, RLS isolation, no update/delete.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.** **Step 5: Commit** `feat(billing): cycles and client plan history`.

---

### Task 7: Pricing and bill functions (pure)

**Files:**
- Create: `csa/billing/pricing.py`, `tests/test_billing_pricing.py`

**Interfaces:**
- Consumes: `Cycle`, `BillingConfig`, `PlanRow`, `Stage`, `Bundle`.
- Produces: `prorated_fee(full_fee: Decimal, days_billed: int, days_in_cycle: int) -> Decimal` (round half-up to 0.01; full cycle returns the fee exactly); `tier_index(executions: int) -> int` (0 for ≤2000, 1 for ≤10000, else 2); `markup_pct(config, stage, bundle, executions, ts) -> Decimal` (looks up the tier row in force); `raw_cost_rm(config, provider, model, prompt_tokens, completion_tokens, ts) -> Decimal | None` (`(prompt × input + completion × output) / 1_000_000` USD times the FX in force; `None` when unpriced); `execution_price(raw_rm: Decimal, markup_pct: Decimal) -> Decimal` (exact, unrounded); `allocate_prices(exact: Sequence[Decimal], total: Decimal) -> list[Decimal]` (largest remainder in units of 0.0001; result sums to `total` exactly; `exact` all zero requires `total == 0` and returns zeros; empty input returns `[]`); `@dataclass(frozen=True) Bill: platform_fee: Decimal; ai_charge: Decimal; total: Decimal` and `bill(*, fee: Decimal, exact_prices: Sequence[Decimal]) -> Bill` (fee rounded 0.01, AI charge = round 0.01 of the sum of exact prices, total = sum of the two rounded figures).

- [ ] **Step 1: Failing tests:** the three vectors (build executions by splitting the workbook raw cost evenly over N executions; assert AI and total within RM0.01); tier boundaries 2,000/2,001 and 10,000/10,001 for every stage/bundle cell of the table; `prorated_fee` for join 15 Aug (21 of 31 days → 99.99×21/31 = 67.73), full cycle exact, 28/30/31-day cycles, termination before the 5th; `allocate_prices` sums exactly to total for random inputs (property test with `hypothesis` if already a dependency, else 200 seeded random cases), zero usage case, one execution; `bill` rounding (fee and AI rounded separately, total is their sum); unpriced model returns `None`.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS.** **Step 5: Commit** `feat(billing): pricing, proration and price allocation`.

---

### Task 8: Usage summary service (ledger → what the API returns)

**Files:**
- Create: `csa/billing/service.py`, `csa/billing/agents.py`, `db/sql/billing/usage_executions.sql` (per-trace aggregate: first `occurred_at`, job of the first row, sums grouped by `(provider, model, price window)` so raw cost can be computed in Python per row), `tests/test_billing_service.py`

**Interfaces:**
- Consumes: Tasks 2, 5, 6, 7.
- Produces (`agents.py`): `AGENT_TITLES: dict[str, str]` mirroring `lib/agent-avatars.ts` for `support`, `assistant`, `hr`, `email`, plus `training` → "Training summary"; `agent_for(job) -> tuple[str, str]` (id, title).
- Produces (`service.py`), all `Decimal`, internal types may carry raw cost and tokens (admin view reuses them):
  `@dataclass ExecutionRow: trace_id; first_at: datetime; job: Job; agent_id: str; raw_rm: Decimal | None; prompt_tokens: int; completion_tokens: int; unpriced_calls: int`;
  `@dataclass CycleUsage: cycle: Cycle; window: tuple[date, date] | None; status: Literal["ok","pending_prices","not_set_up"]; final: bool; executions: list[ExecutionRow]; priced: list[AllocatedExecution]` where `AllocatedExecution` adds `markup: Decimal` and `price: Decimal` (4dp, allocated); totals `bill: Bill | None`, `by_agent: list[AgentTotal(agent_id, title, executions, price)]`, `daily: list[DailyTotal(day: date, price: Decimal)]` covering **every day of the window**;
  `async def cycle_usage(conn, client_id: UUID, cycle: Cycle, *, now: datetime) -> CycleUsage`; `async def list_cycles(conn, client_id, now) -> list[CyclePick(cycle, total: Decimal | None)]`.
  Rules: executions are distinct `trace_id` whose **first** row's KL date lies in the billed window; the execution's job is its first row's job; stage and bundle come from `plan_for_cycle`; markup from the final execution count (`markup_pct` of the cycle's tier); fee from `fee_for` and `prorated_fee` over the window; an execution with any unpriced call is excluded from the AI charge and flagged (status `pending_prices` for the cycle); `final` is `now >= cycle.end`. `by_agent` and `daily` are sums of the allocated prices, so they add up exactly.

- [ ] **Step 1: Failing tests** (Docker, synthetic ledger rows plus config seeds): cycle boundary assignment at 15:59:59Z/16:00:00Z; an execution whose rows straddle the boundary goes to its first row's cycle; two jobs on different traces counted separately, one trace with three rows counted once; zero-usage client with a plan: bill is the fee only, `daily` has one zero entry per day of the window; not set up: `status="not_set_up"`, no bill; unpriced model: `pending_prices`, priced part still totals, unpriced count exposed; tier steps down when the execution count crosses 2,000 and earlier executions are repriced in an open cycle (assert a closed cycle is unchanged by later config rows via effective dates); by_agent and daily sum exactly to `ai_charge`; usage after termination is returned separately (`usage_after_termination: int`) and not billed; fallback-provider job is one execution with two priced rows.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** (SQL does the per-trace grouping, Python prices). **Step 4: Run, expect PASS.** **Step 5: Commit** `feat(billing): cycle usage summary from the ledger`.

---

### Task 9: Client API (`/console/v1/billing`)

**Files:**
- Create: `csa/console/billing.py`, `tests/test_console_billing.py`, `tests/test_console_billing_contract.py`
- Modify: `csa/console/router.py` (`router.include_router(build_billing_router())`), `docs/console_openapi.json` (regenerate with `scripts/export_console_openapi.py`; `tests/test_console_contract_snapshot.py` guards it)

**Interfaces:**
- Consumes: `cycle_usage`, `list_cycles`; the actor and tenant helpers (`csa.console.access.authenticate`, `tenant`, `ConsoleActor`, `Role.TENANT_ADMIN`).
- Produces Pydantic models, all `ConfigDict(extra="forbid")`: `UsageOut(cycle_start: date, cycle_end: date, billed_from: date | None, billed_to: date | None, executions: int, ai_usage_price: Decimal, platform_fee: Decimal, total: Decimal, final: bool, status: Literal["ok","pending_prices","not_set_up"], by_agent: list[AgentUsageOut], daily: list[DailyUsageOut])`; `AgentUsageOut(agent_id, title, executions, price)`; `DailyUsageOut(day, price)`; `ExecutionOut(at: AwareDatetime, agent_id, agent_title, job_label, price: Decimal | None)`; `ExecutionsPageOut(items, next_cursor: str | None)`; `CyclePickOut(start: date, end: date, total: Decimal | None, final: bool)`. Routes: `GET /console/v1/billing/usage?cycle=YYYY-MM-DD`, `GET /console/v1/billing/usage/executions?cycle=&cursor=&limit=`, `GET /console/v1/billing/usage/cycles`. Access: client Admin (`Role.TENANT_ADMIN`) for their own tenant, or platform admin with `client_id`; everyone else 403. The execution cursor encodes `(first_at, trace_id)` and pages in descending order by that pair, filtered to executions that existed at the cursor's issue time only by the keyset itself (so new executions appear at the head and never duplicate or skip). A `cycle` that is not a 5th returns 422; a future cycle returns 404.

- [ ] **Step 1: Failing tests.** `test_console_billing.py`: Admin sees own usage; Member gets 403; another tenant's cycle never appears (RLS plus explicit client scoping); `cycle` default is current; non-5th date 422; pagination stable while a new execution is inserted between pages (no duplicate, no skip); picker lists cycles with a plan or usage. `test_console_billing_contract.py`: walk every client response model's JSON schema and assert no property name matches `token|raw|cost|markup|margin|usd` (case-insensitive) and that none of `UsageOut`'s example payload contains those substrings; log capture during a request holds no token counts.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement the router** with `tx(pool, client_id)` and the service. **Step 4: Regenerate OpenAPI; run the new tests and `tests/test_console_contract_snapshot.py`.** Expected: PASS. **Step 5: Commit** `feat(console): client billing API`.

---

### Task 10: Platform-admin API (overview, plan, prices, FX)

**Files:**
- Modify: `csa/console/billing.py`; Create: `tests/test_console_billing_admin.py`

**Interfaces:**
- Produces (platform admin only, `HTTPException(403, "platform_admin_only")` otherwise; use `actor.platform_admin` like `csa/console/channels.py:79`): `GET /console/v1/billing/admin/overview?cycle=` → per client `{client_id, name, stage, bundle, executions, prompt_tokens, completion_tokens, raw_cost_rm, billed_total, ai_margin_rm, cost_per_execution_rm, workbook_cost_per_message_rm: "0.0167", unpriced_calls, warnings: list[Literal["pilot_ended","usage_after_termination","not_set_up_with_usage","pending_prices"]]}`; `POST /console/v1/billing/admin/clients/{client_id}/plan` body `{stage, bundle, effective_from: date, reason: str}` → calls `insert_plan` (Task 6), maps `PlanRuleError` to 422 with its code; `POST /console/v1/billing/admin/prices` body `{provider, model, input_usd_per_mtok, output_usd_per_mtok, effective_from}` and `POST /console/v1/billing/admin/fx` body `{usd_to_myr, effective_from}`, both applying `check_new_price_date` (422 `shadow_rule`) and writing an audit row. All writes are CSRF-protected the way the existing console writes are. Overview reads across tenants through the owner/admin pool path the console's other platform-admin reads use (read `csa/console/channels.py` and `csa/console/business.py:258`); it must not widen what `csa_app` can read.

- [ ] **Step 1: Failing tests:** non-platform admin 403 on all four; overview carries tokens and raw cost and `ai_margin = ai_charge − raw_cost`; plan POST rule violations map to 422 codes from Task 6; a plan POST audits before and after; price back-dated before the earliest row accepted and the previously unpriced execution becomes priced in the next overview; price inside an existing range 422; FX likewise.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS; regenerate OpenAPI.** **Step 5: Commit** `feat(console): platform-admin billing overview and plan, price and FX writes`.

---

### Task 11: Credit tab (client web)

**Files:**
- Create: `CSA_WebConsole/csa-console/web/app/portal/credit/page.tsx`, `lib/credit-api.ts`, `lib/credit-view.ts`, `tests/credit-view.test.mjs`
- Modify: `lib/navigation.ts` (add `"credit"` to `NavKey`, a nav link "Credit" with no package gate, shown to the Admin role only; keep href-first child literals and the regex in `tests/test_workspace_contracts.py` working), any nav test that lists keys

**Interfaces:**
- Consumes: the Task 9 endpoints (types mirror the Pydantic models; amounts arrive as decimal strings and are formatted, never summed, in the browser).
- Produces: `lib/credit-view.ts` pure helpers (unit-tested): `formatRm(value: string, places: 2 | 4): string` (`"RM 1,234.50"`), `cycleLabel(start: string, end: string): string` (`"5 Sep to 5 Oct"`), `banner(usage): string | null` ("Bill so far, final on 5 Oct" for an open cycle, none for a closed one), `proratedNote(usage): string | null` ("Billed from 15 Aug"), `stateMessage(status): string | null` ("Billing not set up yet" / "Some usage is still being priced"), `dailySeries(usage)` and `agentSeries(usage)` mapping to chart data. The page: cycle picker, four cards (executions, AI usage price, platform fee, bill so far), per-agent table with avatars from `lib/agent-avatars.ts`, paginated executions list (time, agent, job label, price at 4 places, "pending" when price is null), two `recharts` charts (daily bars; horizontal bars by agent), each with a visually hidden data table as the text alternative and a non-colour cue (value labels). Before writing chart code, load the `dataviz` skill; follow existing design system, no new look (read a recent portal page such as `app/portal/subscriptions/page.tsx`). Read the relevant Next.js guide under `node_modules/next/dist/docs/` first, as `web/AGENTS.md` demands. No token figure anywhere.

- [ ] **Step 1: Failing unit tests** `tests/credit-view.test.mjs` (the web suite runs `node --test tests/*.test.mjs`; see existing `.test.mjs` files for how they import TypeScript helpers): each helper above including a daily series with zero days and an open vs closed banner; plus a contract assertion that `lib/credit-api.ts` types have no field named like `token`, `cost`, `markup` or `margin`.
- [ ] **Step 2: Run** `npm test` from `CSA_WebConsole/csa-console/web`. Expected: FAIL.
- [ ] **Step 3: Implement** helpers, API client, nav entry, page.
- [ ] **Step 4: Run** `npm test`, `npm run typecheck`, `npm run lint`, and `bash <wrapper> uv run pytest tests/test_workspace_contracts.py tests/test_console_shell_browser.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(web): Credit tab`.

---

### Task 12: Platform-admin billing page and plan editor

**Files:**
- Create: `CSA_WebConsole/csa-console/web/app/(admin)/billing/page.tsx`, `lib/billing-admin-api.ts`, `tests/billing-admin-view.test.mjs`
- Modify: `lib/navigation.ts` (`NavKey` `"billing"`, platform-admin nav), the admin clients/tenants page (add a "Billing plan" editor: stage, bundle, effective date, reason, posts to the plan endpoint and shows the 422 code as plain English)

**Interfaces:**
- Consumes: Task 10 endpoints. Shows per client raw cost, tokens, billed total, AI margin, cost per execution against RM0.0167, warnings, and forms for price and FX (with the shadow-rule error shown plainly). Plain English copy, existing design system.

- [ ] **Step 1: Failing unit tests** for the warning-to-sentence mapping, the margin and cost-per-execution formatting, and the plan form's error-code messages. **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run** `npm test`, `typecheck`, `lint`. Expected: PASS. **Step 5: Commit** `feat(web): platform-admin billing overview and plan editor`.

---

### Task 13: Langfuse over OTLP, generations, gateway sink (last)

**Files:**
- Modify: `csa/core/langfuse.py`, `pyproject.toml`, `uv.lock`, `apps/worker/main.py:~715` (pass the sink to `LedgerMeter(pool, mirror=sink)`), `apps/gateway/main.py` (build a `LangfuseSink` when `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` are set; pass it as the gateway `LedgerMeter` mirror; close it at shutdown), `tests/test_langfuse.py` (rewritten)

**Interfaces:**
- Consumes: `GenerationMirror`, `MeterContext`, `Usage`.
- Produces: `LangfuseSink` keeps `send_event(...)` and `send_test_trace(...)` signatures (`csa/graph/events.py` and the console test-connection call unchanged) and gains `def submit(self, ctx: MeterContext, usage: Usage) -> None` implementing `GenerationMirror`. Transport: a dedicated `TracerProvider` (not the global one, so `csa/core/tracing.py` is untouched) with `OTLPSpanExporter(endpoint=f"{base}/api/public/otel/v1/traces", headers={"Authorization": "Basic …", "x-langfuse-ingestion-version": "4"})` behind a `BatchSpanProcessor` with bounded queue; `send_event` becomes an OTel span on the trace `trace_id`; `submit` emits a generation span with model, input and output token usage, `user.id = client_id`, session `conversation_id` and **no prompt, reply or cost** attributes. Each generation also carries the `job` as the observation name, the agent id (`support`, `assistant`, `hr`, `email`, `training`) and the job label as tags or metadata, and the trace name is the job, so Langfuse can be sliced by client, agent and job (owner 2026-11-12: leverage Langfuse). **Builder: read https://langfuse.com/integrations/native/opentelemetry/migration-to-v4 and the OTel property mapping page and use Langfuse's documented attribute names (`langfuse.observation.type`, `gen_ai.usage.*`, `langfuse.user.id`, … as they stand today); do not guess.** Add `opentelemetry-exporter-otlp-proto-http==1.45.1` (matches the pinned sdk) and `uv lock`. `_post_batch` is removed; `send_test_trace` returns `(ok, trace_id)` by force-flushing the provider and reading the exporter result. `submit` and `send_event` never raise or block the caller.

- [ ] **Step 1: Failing tests** (rewrite `tests/test_langfuse.py`, using an `InMemorySpanExporter` injected through a constructor argument): generation span carries model and token usage and `user.id`; no attribute contains prompt, reply or cost; `send_event` signature unchanged and emits a span; an exporter failure is logged and not raised; a full queue drops with a logged warning; the OTLP endpoint URL and headers are exactly as above (assert on the exporter configuration); `PostgresEventSink` forwarding still works.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement; `uv lock`.** **Step 4: Run** `tests/test_langfuse.py`, `tests/test_graph_events*.py` (whatever exists via `grep -rl PostgresEventSink tests`), `make lint`. Expected: PASS. **Step 5: Commit** `feat(langfuse): export traces and generations over OTLP`.

---

### Task 13b: Ledger-versus-Langfuse reconciliation (admin)

**Files:**
- Create: `csa/billing/langfuse_check.py`, `tests/test_billing_langfuse_check.py`
- Modify: `csa/console/billing.py` (admin overview gains the check), `CSA_WebConsole/csa-console/web/app/(admin)/billing/page.tsx` and `lib/billing-admin-api.ts` (show it)

**Interfaces:**
- Consumes: Task 13's sink credentials (`LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_BASE_URL`), the ledger token sums from Task 8.
- Produces: `async def langfuse_tokens(http: httpx.AsyncClient, *, base_url, public_key, secret_key, client_id: UUID, start: date, end: date) -> LangfuseTotals | None` reading input and output token totals for one user id (`user.id = client_id`) over the window from Langfuse's public Metrics (or observations) API; `None` plus a logged error when Langfuse is unreachable, unconfigured or answers an error, never a guess. `def drift(ledger: tuple[int, int], langfuse: LangfuseTotals | None) -> Literal["match","drift","unavailable"]` (`match` within 1% or 100 tokens, whichever is larger, since export is batched and best-effort). The admin overview adds `langfuse_check` per client; `drift` adds the warning `langfuse_drift`, `unavailable` shows "Langfuse not reachable" and never changes any billed figure. The ledger stays the authority; this check only reports. **Builder: read Langfuse's current Metrics/observations API docs, and use the documented endpoint, filters and field names; do not guess.** The check is requested on demand by the admin page, not on every overview load, to keep the overview fast.

- [ ] **Step 1: Failing tests:** `drift` boundaries (equal, within tolerance, outside, `None` gives unavailable); `langfuse_tokens` against `httpx.MockTransport` (request carries Basic auth and the client filter; a 500 returns `None` and logs; unconfigured keys return `None` without a request); the overview response for a client reports `unavailable` when Langfuse is down while billed figures are unchanged.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement.** **Step 4: Run, expect PASS; regenerate OpenAPI; `npm test`.** **Step 5: Commit** `feat(billing): reconcile the ledger with Langfuse`.

---

### Task 14: Manual-test seed, end-to-end check, handoff (no PR)

**Files:**
- Create: `scripts/seed_credit_demo.py`, `tests/test_seed_credit_demo.py`, `docs/superpowers/credit-monitoring-manual-test.md`

**Interfaces:**
- `scripts/seed_credit_demo.py`: refuses to run unless `DATABASE_URL`'s host is `localhost`/`127.0.0.1` (the isolated dev Postgres); using `scripts/seed_dev.py` conventions, inserts for the dev client(s): a pilot Individual plan row (join date inside the current cycle), a collaborator Complete plan for a second client, and a few hundred synthetic `ai_usage_events` rows (all seven jobs, both providers, one multi-call trace, one trace straddling a boundary, one unpriced model name) spread over the current and previous cycle; idempotent (a second run adds nothing).
- The manual-test doc lists, as numbered copy-ready commands: `make up`, `make db-reset`, the seed script, `make run-dev`, how to sign in as a client Admin and as a platform admin, what each Credit-tab state should look like, and a checklist (cards, per-agent table, executions, both charts, cycle picker, "not set up" client, pending prices, no tokens anywhere in the network tab).

- [ ] **Step 1: Failing test** `tests/test_seed_credit_demo.py`: refuses a non-local DSN; running twice produces the same row counts; resulting `cycle_usage` for the demo client is `status="pending_prices"` before the missing price is added via the admin price endpoint's shadow rule and `"ok"` after.
- [ ] **Step 2: Run, expect FAIL.** **Step 3: Implement** the script and doc.
- [ ] **Step 4: Full verification:** `bash <wrapper> make lint`; `bash <wrapper> uv run pytest` on every test file added or changed in Tasks 1–14 (list them by `git diff --name-only 942e765 -- tests`); `make db-reset`; the web `npm test`, `typecheck`, `lint`, `build`. Expected: all green. Start the dev stack and walk the manual-test checklist once yourself, including confirming in the browser network tab that no response contains token, raw cost, markup or margin fields.
- [ ] **Step 5: Commit** `feat(billing): demo seed and manual-test guide`. Then stop: report the results and the manual-test doc to the owner. **Do not push or open a PR** until the owner has tested.

---

## Self-review notes

- **Spec coverage:** capture hook and context (T2–T4), ledger (T1), price/FX/fee/markup tables and shadow rule (T5), plan history rules 1–7 (T6), pure billing and allocation (T7), per-agent/daily/cycle summary and unpriced flow (T8), client API with contract test (T9), admin API (T10), Credit tab with two charts (T11), admin page (T12), OTLP last (T13), test vectors (T7), slice-2 hand-off needs only `cycle_usage` (T8). Spec's "async with meter_context" became a sync context manager; a `ContextVar` set needs no await.
- **Plan decisions the spec left to the builder** are marked "Builder:" (provider usage fields, Langfuse attribute names, seed prices, LangGraph context inheritance, audit helper).
- **Known risk to watch in Task 4:** LangGraph node tasks might not inherit the `ContextVar`; the test is written to expose it and names the fallback (bind inside the node wrapper).
