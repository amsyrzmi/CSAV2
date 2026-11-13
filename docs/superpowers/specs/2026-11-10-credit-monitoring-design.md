# Credit monitoring: token metering, billing and the Credit tab (slice 1 of 2)

Date: 2026-11-10, revised 2026-11-12 (twice) after the owner's answers. Branch
`feat/credit-monitoring`, based on 79aa114; the builder rebases onto current main first and confirms
migrations 0550–0559 are still free (main has moved).

Slice 1 (this document): meter every model run, price it per token with the owner's markup table,
add the platform fee, and show each client a Credit tab with their executions, each with its price,
and the bill they will receive. Slice 2 (own design later): a credit agent that staff talk to on
Telegram and WhatsApp.

**Owner decision 2026-11-13:** the client does not see the per-execution list; the bill line is
called "AI Usage Charges"; the client keeps the bill, per-agent and per-day charges and the
execution count, and never sees a model or provider.

## Intent and why

Fourstacks bills each client a platform fee plus their AI usage with a markup (owner's pricing
workbook: Model E for the paid pilot, "Recommendation", and "Early Collaborator AI markups, Option
3" for the early collaborator plan; nothing above Model E applies). Today nothing records usage, so
none of that can be billed or shown. The owner wants each client to see their own usage and the
bill it adds up to, and Fourstacks to see cost and margin per client, without exposing server or raw
provider cost to clients. There is no invoice document in this slice: the tab is the view of the
bill to come.

What the code does today (checked 2026-11-10 and 2026-11-12):

- `GroqClient.json_completion` and `GeminiClient.json_completion` read only the reply text. The
  response's token `usage` is dropped.
- `agent_runs` has `prompt_tokens`/`completion_tokens`, but `PostgresRunRecorder` writes 0 to both,
  and the table is purged after 30 days (`schema_baseline.sql`, retention list).
- `clients.tokens_per_day` (default 400000) exists and nothing reads it. Not used here.
- `LangfuseSink` (`csa/core/langfuse.py`) exports spans only: no model, no usage, no generations.
  It is built only in the worker (`apps/worker/main.py:715`), for graph-node events. The gateway
  (workspace assistant, training summary, HR assistant, email assistant) has no Langfuse wiring.
  Failures are logged and swallowed.
- **The sink posts to Langfuse's legacy ingestion endpoint (`/api/public/ingestion`), which
  Langfuse Cloud removes on 2026-11-16** (the date recorded in `LG15_console_turnover.md:605`; the
  deployment uses `jp.cloud.langfuse.com`). Per Langfuse's documentation of v4, that endpoint keeps
  answering 207 while rejecting every event type except `score-create`; the replacement is OTLP over
  HTTP. Sources: [Langfuse compatibility page](https://langfuse.com/docs/compatibility),
  [migrate ingestion to v4](https://langfuse.com/integrations/native/opentelemetry/migration-to-v4).
  `_post_batch` (`csa/core/langfuse.py:162`) treats any 207 as success and never reads the body, so
  after the sunset the worker would log "ingested" while Langfuse drops the events. The builder
  verifies this against the live endpoint behaviour.
- Twelve `json_completion(` call sites: `csa/decide/llm.py` (6, including the compose fallback),
  `csa/workspace/planner.py`, `csa/hr/assistant.py`, `csa/training/summary.py`, `csa/email/service.py`
  (3: thread analysis at ~1115, reply draft at ~1442, batch compose at ~1482). Providers are built in
  `apps/worker/main.py` (Gemini fallback, Groq) and `apps/gateway/main.py` (five `GroqClient`s),
  plus `csa/evals/decisions.py`.

## The pricing rules (owner's workbook, and the owner's answers of 2026-11-12)

**Billing cycle.** A cycle runs from the 5th of one month to the 5th of the next, midnight
Asia/Kuala_Lumpur: 5/8 to 5/9, then 5/9 to 5/10. A client who joins mid-cycle is billed only from
their start date to the next 5th (join on 15/8: first bill covers 15/8 to 5/9, the next 5/9 to
5/10). A client who terminates mid-cycle is billed from the cycle's start to the termination date.

**Platform fee, per full cycle, no setup fee:** pilot RM99.99; early collaborator RM119.99;
terminated RM0. For a partial first or last period the fee is prorated by days: fee × days billed ÷
days in that full cycle, rounded to RM0.01 (owner 2026-11-12: prorate for now; the platform-fee
policy will be discussed later, so the whole rule lives in one function, `prorated_fee`, and the
tests pin it). The pilot lasts three months from the start date; a
client who finishes the pilot either continues as an early collaborator or terminates; there is no
other plan.

**Execution.** One execution is one job: all the model calls made to do one thing, such as one
email summary, one customer reply or one assistant request. Its tokens are every model call's tokens
added together, and it is charged whether or not it is a chat message. Technically, one distinct
`trace_id` in the ledger, so a job that makes three model calls is one execution, a redelivered
event reuses its trace and counts once, and a job that falls back to the second provider is still
one. A job belongs to the cycle in which its first model call happened.

**AI charge:** each execution's raw provider cost × (1 + markup), in RM, is that execution's price.
The markup depends on stage, bundle and the number of executions in the cycle. The bundle
(Individual, Curated, Complete) selects the markup column; it is not a label. The workbook lists
five volume rows, but three pairs are identical, so the table is three tiers (percent):

| Executions in the cycle | Pilot: Individual / Curated / Complete | Collaborator: Individual / Curated / Complete |
|---|---|---|
| up to 2,000 | 75 / 70 / 67.5 | 85 / 80 / 77.5 |
| 2,001 to 10,000 | 70 / 65 / 65 | 80 / 77.5 / 77.5 |
| over 10,000 | 65 / 65 / 65 | 77.5 / 77.5 / 77.5 |

The same table applies to every client; a per-client override can come later and is not built now.
The thresholds are not prorated for a partial period (my call). The whole cycle is priced at the tier
its final execution count falls in, as the workbook prices a whole month at one volume. During the
cycle the tab prices at the current tier; as volume grows the tier can only step down, so earlier
executions' prices can fall within the open cycle. A closed cycle never changes.

**Bill** = platform fee (rounded to RM0.01) + AI charge (the sum of the executions' exact prices,
rounded to RM0.01), and the total is the sum of those two rounded figures.

**What the client sees** (owner 2026-11-12): only calculated, marked-up prices. Never the raw
token price, never raw cost, and never token counts: the client API and screen carry no token
figure at all. The Credit tab shows, for a billing cycle:

- Cards: executions, AI usage price, platform fee, and the bill so far (the total).
- **Per AI agent:** a table of each assistant the client used: its executions and its price.
- **Per execution:** a paginated list: when, which AI agent, what kind of job in plain words, and
  its price.
- **Two charts:** (1) AI usage price per day across the cycle (bars); (2) AI usage price by AI agent
  (horizontal bars). Both plot prices only, at the cycle's current tier.

**Owner decision 2026-11-13:** the client does not see the per-execution list; the bill line is
called "AI Usage Charges"; the client keeps the bill, per-agent and per-day charges and the
execution count, and never sees a model or provider.

Execution prices are shown to RM0.0001 and allocated (largest remainder, in units of RM0.0001) so
that the listed prices, the per-agent prices, the daily prices and the AI charge all add up
exactly. **Never shown to a client:** raw provider price or cost, tokens, the markup percentage,
margin, AWS cost (RM55/month in the workbook). Tokens stay in the ledger and in the platform-admin
view.

**"AI agent" means the assistant the client uses, not a spoke.** The model calls of a customer
turn are made by the coordinator's decision layer (`csa/decide/llm.py`), not by the spoke agents
(orders, tickets, knowledge), so their cost cannot be split by spoke without inventing an
allocation. The agents the client sees in the tab are the ones in the console's own agent titles
(`AGENT_TITLES` in `lib/agent-avatars.ts`): Customer support assistant, Workspace assistant, HR
assistant, Email assistant, plus Training summary (no title exists for it today). Each `job` maps
to one agent in code, not stored, so a rename does not rewrite history.

**Test vectors** (the workbook rounds raw cost to cents, so reproduce within RM0.01):
pilot Individual, 10,000 executions, raw RM167.37 gives AI RM284.52 and bill RM384.51;
collaborator Complete, 20,000 executions, raw RM334.73 gives AI RM594.15 and bill RM714.14;
pilot Individual, 1,000 executions, raw RM16.73 gives AI RM29.28 and bill RM129.27.
The workbook implies a raw cost of about RM0.0167 per message; the admin view shows the real cost
per execution so the owner can see whether the workbook's margin holds (an execution may use more
or fewer model calls than the workbook assumed).

## Goals

- Every token the platform spends for a client, on any surface and either provider, becomes one
  ledger row in our database, and a generation in Langfuse tagged with the client.
- A client Admin sees, for the open and past cycles: executions, the price of each execution, the
  price per AI agent, two charts, platform fee and total bill, as prices only (no tokens).
  **Owner decision 2026-11-13:** no price of each execution (no per-execution list); the bill line
  is called "AI Usage Charges"; the client keeps the bill, per-agent and per-day charges and the
  execution count, and never sees a model or provider.
- A platform admin sees every client's raw cost, tokens, billed amount, AI margin and real cost per
  execution, and can set each client's plan.
- The Langfuse sink works after 2026-11-16 (OTLP), shipped with the Credit tab (owner 2026-11-12).
- The workbook's three test vectors pass.

## Non-goals

- No invoice: no document, number, print page, issuer details or stored statement. The tab is a
  live view.
- No payments, no payment provider, no dunning. Same stance as the subscriptions project.
- No tax: no SST, withholding tax or LHDN e-invoice. The workbook flags tax and the RM500k
  registration threshold as to-do.
- No AWS cost allocation. Voice transcription (billed per audio second) and embeddings (self-hosted)
  are not token-metered and stay unmetered here.
- No per-client markup override, no budgets or caps, no alerts, no credit agent (slice 2).
- No forecasting of the rest of the cycle: the open cycle shows the bill so far.
- No reinstatement after termination (the workbook says it needs expressly agreed terms).

## Anti-goals (a failure even if tests pass)

- A model run that is not recorded (a new call site, a new entry point, a retry).
- Raw price or cost, token counts, markup percentage or margin reaching a client's browser, API
  response or logs.
- One client's usage visible to another, or a non-Admin member seeing billing.
- Prompt or reply text sent to Langfuse or stored in the ledger.
- A closed cycle's figures changing because of a later edit (plans, prices, FX, fees and markups
  are append-only and effective-dated, and the rules below stop edits from reaching closed cycles).
- A silent fallback: a call with no client context, or a model with no price, must be loud.

## Approaches considered

**Capture.**
1. *Hook inside the provider clients, context in a `ContextVar`* (chosen, owner approved
   2026-11-10). Both clients record after every response that carries usage. Entry points bind
   client, trace and job once. No call site can be missed.
2. Pass a required metering argument into every `json_completion` call. Explicit, but it changes
   the `JsonCompletionClient` protocol and twelve call sites, and the PR becomes much larger.
3. Langfuse only, console reads its API. Rejected by the owner: billing figures cannot depend on a
   best-effort export. The owner chose both: our database is the authority, Langfuse the mirror.

**Pricing.**
1. *Ledger stores tokens only; cost is a pure function of tokens, an append-only price table and an
   append-only FX table, applied at read time* (chosen, owner agreed 2026-11-12). A model added late
   can be priced afterwards.
2. Snapshot cost on each row at write time. Rejected: a missing price at call time would either
   fail the customer's turn or write a bad number forever.

**Showing the bill.** Computed live from the ledger and the effective-dated inputs (chosen, owner
2026-11-12: no invoice). A stored statement was in the first draft and is removed.

**Langfuse transport.** OTLP/HTTP replaces the legacy endpoint (forced by the 2026-11-16 sunset).
Sending generations on the legacy endpoint was rejected: it stops working on that date. Shipping the
migration inside the Credit PR rather than ahead of it (owner 2026-11-12) means Langfuse receives
nothing from the worker between 2026-11-16 and the merge; nothing customer-facing or billing-related
is affected, because the ledger is our own table.

## Design

### Package `csa/metering/`

- `context.py`: `MeterContext(client_id, trace_id, job, conversation_id | None)` and
  `async with meter_context(...)` over a `ContextVar`. `job` is a closed set that also gives the
  client-facing label: `customer_turn` ("Customer reply"), `workspace_assistant` ("Assistant
  request"), `hr_assistant` ("HR assistant"), `email_analysis` ("Email summary"), `email_reply`
  ("Email reply draft"), `email_compose` ("Email compose"), `training_summary` ("Training
  summary"). The builder confirms the email split against the three call sites. A second map,
`job` to agent, gives the AI agent (`support`, `assistant`, `hr`, `email`, and training summary with
no avatar): `customer_turn` to support, `workspace_assistant` to assistant, `hr_assistant` to hr,
the three email jobs to email.
- `meter.py`: `UsageMeter` protocol with `record(Usage)`; `Usage(provider, model, prompt_tokens,
  completion_tokens, schema_name)` (completion includes reasoning/"thinking" tokens, because the
  provider bills them; builder verifies each provider's field names against current docs:
  Groq `usage.prompt_tokens`/`completion_tokens`, Gemini `usageMetadata.promptTokenCount`/
  `candidatesTokenCount`/`thoughtsTokenCount`).
- `LedgerMeter`: writes one `ai_usage_events` row through `tx(pool, client_id)`, then hands the
  same fact to the Langfuse mirror. `NullMeter`: explicit no-op, used only by `csa/evals`.
- A call with no bound context raises `MeteringContextMissing`. It is a programming error: all
  twelve call sites are known, the source-scan and per-entry-point tests catch a miss in CI, and a
  new job fails on its first call in staging.
- A ledger write failure does **not** fail the turn (owner approved 2026-11-12: the provider has
  already billed the tokens, and failing the turn only invites a retry that bills them again).
  The write runs in its own short transaction, never the caller's (a rollback there must not lose
  usage). On failure it retries once, then logs at ERROR with the full usage payload (tokens,
  client, trace, model: no text), so the row can be recovered from logs, and the Langfuse mirror
  still receives the generation. Loud, not silent.
- The Langfuse mirror is best-effort: queued, bounded, failures logged, never raised, and never in
  the latency path of the call.

### Provider clients

`GroqClient` and `GeminiClient` take a required keyword `meter: UsageMeter`. They record on every
HTTP response that carries usage, including retried attempts and replies that then fail schema
validation (those tokens were billed), and not on responses without usage. An architecture test
(a source scan, like `tests/test_bookings_bot_schema.py`) fails if any `GroqClient(` or
`GeminiClient(` in `apps/` or `csa/` omits `meter=`. Wiring: `apps/worker/main.py` and
`apps/gateway/main.py` each build one `LedgerMeter` from their pool and Langfuse sink; the gateway
also builds a `LangfuseSink` (new) when keys are set.

### Entry points that bind context

Worker customer turn (`IngressWorker`, where trace and client are known); `create_planned_turn`
(`csa/workspace/service.py:1260`); `HrAssistant.run`; `training.summary.summarise` callers in
`csa/training/service.py`; the three `EmailService` model calls. Each already holds the client id
and runs inside `tx(pool, client_id)`. Gateway jobs mint one trace per request. The builder
confirms that LangGraph nodes see the bound context (same task, or tasks created inside it), that
a retried or redelivered worker turn reuses its trace id (an execution counts once), and adds a
test per entry point asserting that a ledger row appears with the right client, job and trace.

### Langfuse (OTLP)

- Migrate `LangfuseSink` to OTLP/HTTP: traces to `<base>/api/public/otel/v1/traces`, Basic auth
  with the existing public and secret keys, header `x-langfuse-ingestion-version: 4` (builder
  confirms against the migration guide linked above). The existing node events become OTel spans
  and the new generations become spans carrying the model, `usage` input and output tokens, session
  and user attributes (`userId = client_id`) in the attribute names Langfuse documents; the builder
  reads the migration guide's mapping and does not guess names.
- Add the pinned `opentelemetry-exporter-otlp-proto-http` (matching the version pin of the OTel
  packages already in `uv.lock`) and update `uv.lock`. Use a dedicated `TracerProvider` for
  Langfuse, not the global one, so the console and gRPC exporters in `csa/core/tracing.py` are
  untouched. A `BatchSpanProcessor` provides the bounded queue.
- Keep `LangfuseSink.send_event`'s signature so `PostgresEventSink` and its call site do not change;
  add `send_generation`. No prompt, reply or customer text, and no cost fields (the ledger is the
  authority). `tests/test_langfuse.py` is rewritten for the new transport.
- Build order inside the project: ledger, billing logic, API and tab first (they depend on nothing
  in Langfuse), the OTLP sink and generations last. Only the last step depends on Langfuse.

### Database (migrations 0550 to 0552; README range added; `EXPECTED_SCHEMA_VERSION` bumped)

- `0550_ai_usage_events.sql`: `ai_usage_events(id bigint identity, client_id, occurred_at
  timestamptz default now(), trace_id uuid, conversation_id uuid null, job text check in the closed
  set, provider text, model text, prompt_tokens int >= 0, completion_tokens int >= 0, schema_name
  text)`; index `(client_id, occurred_at)` and `(client_id, trace_id)`; tenant-isolation RLS,
  `csa_app` insert and select, `csa_ro` select, no `FORCE`. Not added to the 30-day retention list.
- `0551_billing_config.sql`: global tables (no client column, readable by `csa_app`), every one
  with `effective_from`, rows never edited or deleted: `ai_model_prices(provider, model,
  input_usd_per_mtok, output_usd_per_mtok, effective_from)`, `billing_fx_rates(usd_to_myr,
  effective_from)`, `billing_stage_fees(stage, monthly_fee_rm, effective_from)` and
  `billing_markup_tiers(stage, bundle, up_to_executions null, markup_pct, effective_from)`. Fees and
  markups are seeded with the numbers above (effective from a date before any usage) and change only
  by a later migration adding new rows. Prices and FX are written through the platform-admin API.
  Seed prices for the configured Groq and Gemini models are the builder's to source and cite
  (values are not in this document on purpose).
  **Shadow rule** (keeps closed cycles stable): a new price or FX row may be back-dated only to a
  date before that model's (or the FX table's) earliest existing row, to fill a gap of unpriced
  calls; otherwise `effective_from` must be today or later (more than one day ahead is refused). A
  row dated inside an existing priced range is refused. Each write is audited.
- `0552_client_billing_plans.sql`: plan history, specified here in full.
  `client_billing_plans(id, client_id, stage in (pilot, collaborator, terminated), bundle in
  (individual, curated, complete), effective_from date, reason text, created_by, created_at)`,
  tenant RLS, written by platform admin only, rows never edited or deleted. A row's
  `effective_from` is a date in Asia/Kuala_Lumpur; a date takes effect at its 00:00.
  1. **Cycles** run from the 5th 00:00 to the next 5th 00:00 (see pricing rules). `cycle_of(ts)` is
     the cycle containing a timestamp's local date.
  2. **First row** starts a client's billing and is the join date: any date within the current
     cycle (not earlier than the current cycle's start, so a closed cycle is never created). The
     first cycle is billed from that date to the next 5th, prorated. Cycles before it show
     "Billing not set up yet" and bill nothing; this also keeps the time before metering existed
     from being billed. Existing clients have no row and stay unbilled until an admin sets one.
     Rows after the first must be `collaborator` or `terminated` or a bundle change (same stage).
  3. **Stage and bundle changes** (pilot to collaborator, bundle change) are dated a cycle start (a
     5th), and the current cycle's start or any later 5th is allowed. A change dated the current
     cycle's start applies to the whole current cycle, fee included. A cycle uses the latest row
     (greatest `effective_from`, then newest `created_at`) that is not `terminated` and whose
     `effective_from` is before the cycle's end. Same-date rows are how a mistake is corrected: the
     newest wins, the old row stays for audit.
  4. **Termination** is dated any day not earlier than the current cycle's start. Service for
     billing ends at that date's 00:00. The final period runs from the cycle start (or the join
     date, if later) to the termination date and is prorated by days. Every later cycle bills RM0
     and no usage; usage still recorded afterwards is shown to the admin as "usage after
     termination". After a `terminated` row no further row is accepted (no reinstatement here).
  5. **Closed cycles are never edited**: no row may take effect earlier than the current cycle's
     start.
  6. **Pilot length** is three months from the first row's date. From the first cycle start on or
     after that point, if the stage is still `pilot`, the admin view shows "pilot ended: convert or
     terminate". Nothing is automatic: billing continues on pilot terms until an admin inserts a
     `collaborator` or `terminated` row (my call: the service is still running, and the owner chose
     "terminate", not "auto-terminate").
  7. Every insert writes an audit row with before and after, using the existing audit mechanism.

### Billing logic (`csa/billing/`)

Pure functions, `Decimal` throughout, no I/O: `cycle_of`, `plan_for_cycle`, `billed_window`
(start and end of the billed period for a client in a cycle), `tier_for(executions)`, `execution_price`,
`bill(...)`, `allocate_prices`. Raw cost per ledger row = (prompt × input price + completion × output
price) / 1,000,000 in USD, converted with the FX row in force at `occurred_at`; the price row is the
one in force at `occurred_at`. An execution's raw cost is the sum over its rows. Executions in a
cycle = distinct `trace_id` whose first row is in the billed window. A row whose model has no price
is "unpriced": the execution shows "pending", the client sees figures for the priced part flagged,
the admin view shows the unpriced count, and the shadow rule lets an admin back-date the missing
price.

### API (`csa/console/billing.py`, mounted under `/console/v1/billing`)

- `GET /usage?cycle=YYYY-MM-DD` (the cycle's start date; default the current cycle): client Admin
  (or platform admin with a `client_id`). Returns the billed window, executions, AI usage price,
  platform fee, total, `final: bool` (false for the open cycle), `status` (`ok`, `pending_prices`,
  `not_set_up`), `by_agent` (agent id, title, executions, price) and `daily` (local date, price) for
  the charts. Never raw price or cost, tokens, markup or margin.
- `GET /usage/executions?cycle=&cursor=`: the paginated list (time, agent id and title, job label,
  price at 4 decimals).
  **Owner decision 2026-11-13:** this route was removed (it answers 404); the client has no
  per-execution list.
- `GET /usage/cycles`: the cycles with a plan or usage and each cycle's total, for the picker.
- Platform admin only: `GET /admin/overview?cycle=` (per client: raw cost, billed, AI margin, real
  cost per execution against the workbook's RM0.0167, unpriced count, warnings such as "pilot
  ended" and "usage after termination"); `POST /admin/clients/{id}/plan` (inserts a row, enforcing
  the rules above); `POST /admin/prices`; `POST /admin/fx`. Every write writes an audit row. The admin overview carries tokens and raw cost.
- Pydantic response models with `extra="forbid"`; a contract test asserts the client models have
  no raw price, cost, token, markup or margin field. OpenAPI document regenerated
  (`docs/console_openapi.json`).

### Web console

- Client: `app/portal/credit/page.tsx`, nav key `credit` ("Credit", Admin role only, no package
  gate), `lib/credit-api.ts`, `lib/credit-view.ts`. Cycle picker labelled with dates ("5 Sep to 5
  Oct"); cards (executions, AI usage price, platform fee, bill so far); the per-agent table with
  each agent's avatar from `lib/agent-avatars.ts`; the two charts built with `recharts` (already a
  pinned dependency, 3.10.1), accessible (text summary or table alternative, colour not the only
  cue), following the existing design system; the paginated executions list; a "Bill so far, final
  on 5 Oct" banner for the open cycle; a first-period note when prorated ("Billed from 15 Aug");
  plain states for "Billing not set up yet" and "Some usage is still being priced". No token
  figure anywhere on this page.
  **Owner decision 2026-11-13:** no paginated executions list on the page; the bill line is called
  "AI Usage Charges"; the client keeps the bill, per-agent and per-day charges and the execution
  count, and never sees a model or provider.
- Platform admin: `app/(admin)/billing/page.tsx` (raw cost, tokens, margin) and a plan editor on the
  clients page.
- Copy is plain English; follow `web/AGENTS.md` and the existing design system, no new look.

### Tests

Billing math against the three vectors and each tier boundary (2,000/2,001, 10,000/10,001);
proration by days for first and final periods (join 15/8 gives 15/8 to 5/9; terminate before the 5th;
a full cycle is exactly the fee; 28-, 30- and 31-day cycles); rounding of fee and AI charge and the
total as their sum; price allocation: execution prices, per-agent prices and daily prices each sum exactly to the AI
charge; `by_agent` and `daily` data for the charts, including days with no usage; plan-history rules 1 to 7
including same-day correction, scheduled future row, refused edit to a closed cycle, no row after
termination, pilot-ended warning; shadow rule for prices and FX; executions counted once per trace
across jobs and retries and assigned to the cycle of their first call; both clients record usage on
success, retry and invalid output; no-context raises; the source-scan test; one entry-point test per
job; ledger-write failure logs and still answers; RLS isolation of ledger and plans; client contract
has no raw price, cost, token, markup or margin field; Langfuse payload has no text and carries usage over
OTLP; unpriced flow. Migration tests as in existing 05xx files. Frontend unit and type tests as
existing.

## Slice 2 (recorded, own design later)

The credit agent is for staff, chatting on Telegram and WhatsApp (owner, 2026-11-12), answering
from the same `csa/billing/` functions through a read-only summary. Prerequisite found in the code:
those channels carry the customer bot only. Telegram accepts private chats from customers, accepts
group messages only as replies to a bot alert, and accepts staff button callbacks; nothing links a
Telegram or WhatsApp sender to a console user. Slice 2 therefore starts with staff identity on
those channels (and which role may ask about billing), then the agent. Slice 1 exposes the summary
function so slice 2 does not recompute anything.

## Open questions for the owner

1. **Is "per AI agent" per assistant (five lines) acceptable?** It is what the code can measure
   exactly. Splitting customer-turn cost across spokes (orders, tickets, knowledge) would need an
   allocation rule and is left out. Not blocking: this document assumes per assistant.

Decided for now, to revisit: the platform fee is prorated by days for partial first and last
periods; the fee policy will be discussed later (owner 2026-11-12).

## Left to the builder

Rebasing and confirming the migration range; provider usage field names and reasoning-token
accounting; seed model prices with sources; OTel attribute mapping for Langfuse and the exporter
version pin; whether LangGraph nodes inherit the bound context and whether a retried turn reuses
its trace; table and index details; frontend component structure; the Langfuse queue's bounds.
