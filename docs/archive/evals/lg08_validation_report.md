# LG08 validation report — 2026-10-12

## Scope and isolation

These tests use generated synthetic tenants and disposable testcontainers with the same
PostGIS/pgvector image as development. No production database, bot, hostname, provider or
Telegram delivery is used. Real gateway routing, secret verification, normalization, tenant
transactions, ingress persistence, worker claims/advisory locks, graph checkpoints, timeline
and outbox writes run locally. Classifier/composer calls in golden cases are cassettes; the
queue load run uses the deterministic no-provider dependencies.

## Reproduction

```sh
uv sync --frozen
make coverage
make load-test
```

Run performance tests without coverage instrumentation or concurrent builds. They exercise a
shared home server, so results are not cloud capacity estimates. Do not redirect these tests
to a live database; their fixtures create and remove their own databases.

## Policy branch coverage

Measured with coverage.py 7.10.7, `--branch --source=csa.graph.policy`:

| Module | Statements | Branches | Missed statements / branches | Coverage |
|---|---:|---:|---:|---:|
| gate | 51 | 18 | 0 / 0 | 100% |
| handoff | 19 | 10 | 0 / 0 | 100% |
| plan_rules | 41 | 22 | 0 / 0 | 100% |
| routing | 42 | 28 | 0 / 0 | 100% |
| **Total** | **153** | **78** | **0 / 0** | **100%** |

The focused command passed 78 tests and deselected two timing benchmarks. The 90% combined
statement/branch floor is enforced by `make coverage` and the CI workflow. Timing benchmarks
run uninstrumented: an exploratory instrumented graph benchmark reached p95 246 ms, which
is not a valid uninstrumented runtime measurement.

## Load measurements

Runner: [test_load_acceptance.py](../../../tests/test_load_acceptance.py).
150 unique Telegram-shaped messages from 150 first-time contacts, evenly scheduled at
50 requests/s for 3 seconds. Initial measurements used `httpx.ASGITransport`;
the socket follow-up below also runs a separate real Uvicorn gateway process. Neither
includes Caddy, Cloudflare or the external Telegram webhook. All responses must be accepted
and non-duplicate.

The synthetic endpoint limit is explicitly raised to 6000/min for this test. Normal endpoint
policy is not changed. Request-start delay and schedule-to-response latency are recorded
so a slow generator cannot make the result look faster.

| Measurement | Result |
|---|---:|
| Offered request rate | 50/s |
| Accepted / persisted events | 150 / 150 |
| Ingress interval | 3.022 s |
| Request-to-response p95 | **109.642 ms** |
| Scheduled-arrival-to-response p95 | **112.704 ms** |
| Generator start-lag p95 | 5.631 ms |
| App pool | 8 connections, pre-opened |
| Queue drain concurrency | 4 |
| Queue drain duration | 73.120 s |
| Durable graph throughput during drain | **2.051 turns/s** |
| Completed events / unique outbound replies / pending deliveries | **150 / 150 / 150** |
| Graph-only p95 (200 turns, fake dependencies, no DB/checkpoint/timeline) | **99.068 ms** |

The queue is drained **after** ingestion, not concurrently. Throughput includes real database
locks, graph checkpoints and timeline transactions but excludes actual agent/model/provider
latency and network delivery. It is a short backlog-drain observation, not sustained capacity.
It does not prove the worker can keep up with 50 turns/s. The queue absorbed the burst without
losing events or creating duplicate replies; delivery was intentionally left pending.

### Cold-pool failures are retained

| Run | Conditions | Ack p95 | Scheduled ack p95 | Result |
|---|---|---:|---:|---|
| Initial fixture | Pool max 4; lint ran concurrently | 238.398 ms | 242.750 ms | Failed 200 ms target |
| Runtime-sized, cold | Pool max 8, starts with 1 connection; no concurrent lint | 451.840 ms | 465.311 ms | Failed 200 ms target |
| Steady state | All 8 connections pre-opened | 109.642 ms | 112.704 ms | Passed |

At the time of these initial measurements the gateway started its pool with `min_size=1`.
No runtime pool-size change was made to obtain that steady-state result. The subsequent
startup change and measured first-burst results are recorded below; simultaneous
ingestion/processing and longer socket-level testing remain open.

## Golden-case coverage

All 13 JSON cases run the real worker, graph, agents, SQL, checkpoints and outbox. Assertions
inspect actual persisted agent runs (including handoff-triggered escalation), not only normal
`run_spoke` timeline steps.

| Cases | Verified invariant |
|---|---|
| order_status_none / one / many | No unnecessary order-id question; deterministic resolution, grounded status, multiple-order buttons |
| order_status_excludes_drafts | Draft/cancelled orders are absent from active-order context and model prompts |
| semantic_regen | Unsupported business claim rejected; correction sees issues; only corrected reply queued |
| semantic_fail_safe / timeout | Repeated rejection or checker outage persists one safe reply and escalation |
| policy_auto / suggest | Same greeting is sent or held according to this tenant's policy |
| policy_first_contact | Persisted first-contact flag causes a pending approval with no customer delivery |
| policy_shadow | Reply computed/verified but no customer message, delivery or approval created |
| injection_muted | Injection escalates once; three later turns make no planner/composer calls or new escalations |
| classifier_timeout | Classifier timeout persists a safe reply, never silence |

The first-contact golden case starts after normalization; contact creation/credential-based
identity and cross-tenant RLS are covered by ingress and tenancy tests, not that golden case.
Muted turns currently still classify before routing; they send no additional acknowledgements.
Do not infer the complete planned escalation-window behavior from this narrower invariant.

## Remaining LG08 acceptance

- The complete matrix still lacks cases and depends partly on future WhatsApp, Gmail, voice,
  calendar, budgets, draft-expiry and staff Edit implementations.
- Wider implemented-path coverage is still needed for typed confirmation, callback after
  cancellation and ticket ownership. Current ownership/expiry and partial-result cases are
  recorded below; lower-level tests are not a substitute for the entire golden matrix.
- Two-tenant scenario insertion is measured below; broader seed data and total empty-database
  schema/migration/seed timing remain unverified.
- No commit/history verification is possible in this non-Git checkout. This report is persisted,
  not committed; the "load report committed" acceptance checkbox remains open.

## Final regression validation

- `uv sync --frozen` passed with exactly pinned `coverage==7.10.7` and updated `uv.lock`.
- `ENV=ci make lint test` passed: Ruff, formatting, Pyright, import boundary, **305 tests**
  (test duration 615.29 s). Load thresholds also passed as part of the uninstrumented full suite.
- `make db-reset` passed on a fresh disposable Postgres: schema 0220 and two seeded tenants.
  The live dev database and bot registration were preserved.
- Dev `/readyz` remained ready. No runtime process restart or production change was needed for
  this test/tooling slice.
- Local Gitleaks 8.24.2 source scan passed with networking disabled and local secrets,
  dependencies and generated caches excluded. Git history was not scanned.

## Gateway startup and callback/ticket follow-up

The gateway now opens all **eight existing pool slots** before its lifespan yields. The
maximum connection count is unchanged; only the minimum changes from one to eight.
Startup connection errors propagate, and the pool is closed even when opening fails.
Worker and delivery pool defaults are unchanged. This trades a small startup wait and seven
additional idle connections per gateway process for predictable first-burst availability.
Allow for that per-process minimum when sizing database connections for multiple replicas.

The load test now uses `create_app()` and its **real lifespan**, with a DSN pointing to the
disposable testcontainer. There is no test-only pool resize. It checks that eight connections
exist at startup and that the pool closes at shutdown, then drains the durable queue separately.

| Follow-up measurement | Result |
|---|---:|
| Actual gateway lifespan startup | 346.742 ms |
| First-burst arrivals | 150 unique requests at 50/s |
| Request-to-response p95 | **139.190 ms** |
| Scheduled-arrival-to-response p95 | **144.328 ms** |
| Generator start-lag p95 | 9.950 ms |
| Ingress interval | 3.034 s |
| Completed / unique outbound replies / pending deliveries | **150 / 150 / 150** |
| Four-way backlog drain | 72.532 s, **2.068 turns/s** |
| Graph-only p95, 200 turns | 96.154 ms |

This fixes the connection-expansion penalty **after successful startup** in the tested ASGI
path. Startup time is reported separately; TCP/proxy latency and simultaneous sustained
worker/provider throughput are still not measured.

Four additional whole-turn cases bring the golden suite to **17**:

- Order Confirm and repeated Confirm: one order remains awaiting staff approval
  (`pending_confirmation`); reservation is retained, and callback acknowledgements use no LLM.
- Draft Cancel and repeated Cancel: one cancelled order, reservation released once, no LLM
  acknowledgement or unintended escalation.
- Ticket `close` alias and `TKT-` identifier: initially prompts for a button without cancellation;
  callback and repeated callback produce one cancellation audit event.
- Repeated same-subject ticket creation: one ticket and creation event; requested urgent
  priority is capped to high.

These callbacks resolve actual tokens from earlier outbound UI and run through the real worker
and graph. Gateway `answerCallbackQuery` and clearing the Telegram keyboard are not exercised
by these offline cases; existing channel tests and previous live evidence cover those surfaces.
Persisted order/ticket statuses, product reservations and ticket audit counts are checked,
not only reply wording. Golden-case IDs in synthetic ticket seeds are not production IDs.

Focused startup/load/golden tests: **24 passed**. Policy coverage with the expanded cases:
**82 passed**, two timing tests deselected; all policy statements and branches remain covered.

Final follow-up validation: `ENV=ci make lint test` passed **311 tests** in 630.60 s, with
lint, formatting, types and import boundaries clean. Disposable `make db-reset` passed again
(schema 0220, two seed tenants), without resetting the live dev-bot database.
Local Gitleaks source scan passed after these changes. Only the isolated dev gateway was
restarted to apply startup pre-opening; `/readyz` returned ready on 0220. Existing dev worker
and delivery processes stayed running. Caddy, Cloudflare and production services were untouched.

## Isolation, seed timing and real-socket follow-up

Five new cases bring the suite to **22** whole-turn golden cases:

| Case | Persisted invariant |
|---|---|
| order_confirm_wrong_contact | Original draft/action/reservation unchanged; tapping contact escalated |
| order_confirm_expired | Expired token cannot confirm; original draft/reservation unchanged |
| order_foreign_lookup | Other contact's order returns not-found; no order detail leaks |
| multi_agent_partial | Missing product plus successful ticket lookup records the agent error and holds the reply under partial-result review policy |
| ticket_cancel_expired | Original ticket stays open; no cancellation audit event |

These tests describe the current callback UX, not an ideal rejection message: invalid/expired
callbacks currently escalate. Expiry is enforced using `expires_at`; the action's stored status
can remain `open`. Ticket-cancellation expiry also creates the escalation ticket. A dedicated
`CONFIRM_REJECTED` acknowledgement is not claimed.

Two synthetic tenant scenarios, with suggest/auto policies and product/order rows, were inserted
in **0.238 s** after schema/migrations. App-role RLS exposed only each tenant's own products and
orders, and both policies were verified in persisted config. This is scenario-insertion time,
not full container startup or the entire planned multi-channel seed matrix.

### Socket harness and retained failures

The TCP test passes a newly bound ephemeral loopback socket to a separate Uvicorn subprocess.
Both database URLs point to its disposable testcontainer. It waits for `/readyz`, uses real
runtime logging/tracing, offers 150 unique arrivals at 50/s, checks both p95 measures, and
terminates/waits for that specific child in cleanup. No fixed dev or production port is used.
The ASGI comparison still stubs logging/tracing; it is not the runtime acceptance proxy.

| Experiment | Ack p95 | Scheduled ack p95 | Result |
|---|---:|---:|---|
| Uvicorn on the generator's pytest loop | 2527.709 ms | 2584.881 ms | Failed; generator lag p95 209.245 ms |
| Separate gateway, original tracing | 1086.254 ms | 1086.667 ms | Failed |
| Separate gateway, asyncio instead of auto loop | 1375.611 ms | 1376.457 ms | Failed; loop override removed |
| Batched console tracing | 519.816 ms | 520.665 ms | Failed |
| Batched compact console tracing | 354.614 ms | 356.534 ms | Failed |
| Request spans only; no ASGI chunk child spans | 239.323 ms | 240.267 ms | Failed |
| Sixteen gateway connections | 310.192 ms | 311.207 ms | Failed; connection increase reverted |
| Consolidated contact resolution, focused run | 187.955 ms | 194.571 ms | Passed, but insufficient repeat margin |
| Same contact change, full-suite repeat | 239.817 ms | 241.504 ms | Failed; 322 other tests passed |
| Contact consolidation + gateway autocommit | **140.408 ms** | **144.002 ms** | Passed |

A diagnostic CPU profile identified console serialization/export overhead; profiler-instrumented
results are not acceptance measurements. Lightweight query timing also found repeated ingress
round trips (roughly 8–15 ms mean per lookup/insert), versus pool-wait p95 14.870 ms. These are
diagnostic observations on a shared host, not a controlled attribution study.

### Persistent fixes and safety checks

- Console and OTLP export are batched. Console spans are compact JSON lines. FastAPI retains
  the server request span, including trace id, duration and status, but omits per-chunk
  receive/send child spans. The persisted agent timeline is unchanged.
- `resolve_telegram_contact.sql` combines known-contact lookup or contact/identity creation.
  When a statement snapshot cannot resolve a concurrent insert, the existing subsequent
  statement path resolves it or rejects an identity mismatch. Tenant RLS remains active.
- Only the gateway pool uses autocommit, avoiding implicit BEGIN/COMMIT for unscoped read-only
  endpoint resolution. All tenant operations, staff callbacks and console queries still use
  explicit `tx()` transactions. Worker/delivery defaults remain unchanged; gateway capacity is
  still eight, not sixteen.
- Tests cover concurrent first messages (one contact/identity/conversation and one first-contact
  flag), stored-name preservation, legacy identity repair, conflict rollback in both pool modes,
  transaction-local tenant GUC reset in both modes, and compact request spans exported off the
  request event loop. The focused ingress/tenancy/gateway/tracing/TCP run passed **25 tests**.

| Final focused TCP measurement | Result |
|---|---:|
| Startup until ready | 5310.635 ms |
| Offered arrivals | 150 at 50/s |
| Ingress interval | 3.037 s |
| Ack / scheduled ack p95 | **140.408 / 144.002 ms** |
| Generator lag p95 | 7.664 ms |
| Completed / unique outbound replies / pending deliveries | **150 / 150 / 150** |
| Four-way backlog drain | 73.805 s, **2.032 turns/s** |

This remains a three-second ingress burst with the worker draining afterward. It does not
establish simultaneous sustained ingress/worker capacity, actual model latency, delivery,
proxy performance or a cloud SLA. The strict 200 ms assertions were not loosened.

### Earlier regression before process-level worker tests

- `ENV=ci make lint test`: **325 passed** in 736.72 s; Ruff, formatting, Pyright and import
  boundaries passed. Both ASGI and TCP latency assertions passed in this full-suite run.
- `make coverage`: **88 passed**, two timing tests deselected; **100%** of 153 policy statements
  and 78 branches, with the 90% floor unchanged.
- Disposable `make db-reset`: passed, schema 0220 and two seed tenants. The live dev database
  and bot registration were preserved.
- Local Gitleaks 8.24.2 source scan: no leaks, networking disabled, with ignored local secrets,
  dependencies and generated caches excluded. No Git history scan/commit is possible here.

The first standalone `make load-test` reproduction after the full suite passed TCP at
**124.791 ms** (scheduled **125.749 ms**) and graph-only at **97.331 ms**, but the shared-loop
ASGI comparison failed at **296.218 ms** (scheduled **341.772 ms**, generator lag **40.632 ms**).
This failure is retained; a passing socket result does not erase it.

An **unchanged** repetition passed all three load tests:

| Measurement | ASGI comparison | Separate TCP gateway |
|---|---:|---:|
| Ack p95 | 187.828 ms | **108.808 ms** |
| Scheduled ack p95 | 190.964 ms | **110.330 ms** |
| Generator lag p95 | 12.877 ms | 3.592 ms |
| Startup | 312.423 ms | 4980.571 ms |
| Ingress interval | 3.014 s | 3.017 s |
| Backlog drain | 73.617 s, 2.038 turns/s | 74.660 s, 2.009 turns/s |
| Completed / unique replies / pending deliveries | 150 / 150 / 150 | 150 / 150 / 150 |

Graph-only p95 in that repetition was **97.839 ms**. The ASGI comparison remains sensitive to
shared-loop/host scheduling; no automatic retry or relaxed threshold was added to the tests.
Three final measured TCP runs passed, as did the TCP check inside the full suite.

Only the isolated dev gateway was restarted with these changes. Local `/readyz` returned
`{"status":"ready","reason":"ok"}`, schema remains 0220, and the listener remains loopback-only
on 18080. Existing worker and delivery PIDs were unchanged. No fresh live Telegram exchange,
Caddy reload, Cloudflare edit, webhook re-registration or production change was performed.

## Simultaneous gateway and worker diagnostic

The previous load measured acknowledgement and then drained the queue. A separate diagnostic
now offers the same 150 synthetic events at 50/s over TCP while four durable coordinator
consumers process them. Each consumer has a separate graph/runtime and checkpoint saver, while
the app-role and checkpoint connection pools are shared. This is a test topology experiment,
not the current worker entrypoint, which is still serial. The new measurement has no latency
pass threshold; it must complete every turn exactly once and reports latency/capacity rather
than disguising an overloaded result as success.

Most recent run:

| Metric | Result |
|---|---:|
| Arrival rate | 50/s for 150 events |
| Ingress interval | 3.194 s |
| Ack / scheduled ack p95 | **782.528 / 840.960 ms** |
| Four consumers, individual `process_once` p95 | 1961.169 ms |
| Post-ingress drain | 64.034 s, approximately **2.34 turns/s** |
| Completed / unique replies / pending deliveries | **150 / 150 / 150** |

Earlier runs of this experiment measured ack p95 from **276 to 410 ms** and drain throughput
around **2.1–2.4 turns/s**; observed latency varied materially on the shared host. All measured
simultaneous runs missed the 200 ms acknowledgement target. Preserve the earlier separate-load
result as the gateway acknowledgement acceptance measurement: it tests 50/s while the durable
queue is empty and processes the backlog after ingress. Do not interpret that acceptance as
proof that the worker keeps pace with 50 turns/s.

Database timeline rows report whole milliseconds, so zero means a duration below 1 ms, not
that a node had no cost. In the diagnostic, typical recorded node means included `load_ctx`
19.1 ms, `gate` 23.1 ms, `finalize` 23.5 ms and `emit` 38.4 ms. Checkpointer instrumentation
recorded 300 `aget_tuple`, 2250 `aput` and 2100 `aput_writes` calls for 150 greeting turns
(about 31 operations per turn). Their respective means/p95 were **10.880/34.158 ms**,
**44.841/119.850 ms** and **86.697/126.521 ms**. The diagnostic gives each consumer an
independent saver instance, so these calls are not serialized through one shared saver. Their
frequency and the measured database write time still make checkpoint persistence a material
part of per-turn work and shared-database contention. A test-only run using one independent
saver per consumer reduced drain time to 63.448 s, but ack p95 reached 409.615 ms; the final
run reached 782.528 ms. This small shared-host sample does not justify changing production
checkpoint or worker concurrency semantics on its own.

Thus the measured design currently separates **fast durable ingress acceptance** from slower
graph execution. Meeting 50 completed turns/s requires a workload-sized database, worker
autoscaling and a checkpoint-throughput design; neither the local dev host nor this short
test establishes that capacity. No lower checkpoint durability or claim-order guarantee was
introduced. A design target is needed before changing those guarantees or setting concurrent
workers by default.

### Worker process coordination tests

`tests/test_worker_multiprocess.py` runs actual spawned OS processes with independent process
pools against the disposable Postgres fixture:

- Two processes share eight same-conversation events. Both claim work; all events complete,
  inbound bodies remain in original order, and each trace has exactly one echo and one pending
  delivery.
- A process is terminated after claiming an event and acquiring its session advisory lock,
  before turn side effects begin. PostgreSQL releases the lock on process death; after the
  stale lease is reaped, a new process completes the event with one inbound/outbound pair and
  one delivery.

Both focused tests passed. These cases demonstrate process-level claiming, ordering, lock
release and recovery for the echo worker path. They do not establish coordinator/checkpoint
parallel throughput, a crash after arbitrary graph side effects, or a sustained capacity target.
The current process entrypoint is still a single consumer; cloud replicas require independent
processes and database connection budgeting as described in the [runbook](../../RUNBOOK.md).

### Final regression after adding process-level tests

- `ENV=ci make lint test`: **328 passed** in 642.73 s; Ruff, format, Pyright and import-linter
  passed. This includes the four load tests and both spawned-process tests.
- `make coverage`: **88 passed**, two timing tests deselected; policy coverage remains **100%**
  for 153 statements and 78 branches.
- `make db-reset` passed only against a disposable Postgres container: schema 0220 and two seed
  tenants. The live dev database was not reset.
- Local Gitleaks 8.24.2 source scan found no leaks; container networking disabled, local
  secrets/dependencies/caches excluded. No Git history scan or commit is available in this
  non-Git workspace.
- Dev `/readyz` remained ready on schema 0220; gateway, worker and delivery processes retained
  their existing PIDs. No webhook, Caddy, Cloudflare, Telegram or production change was made.
