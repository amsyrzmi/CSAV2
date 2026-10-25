# LG08 — Tests, seed data, evals (runs alongside LG03–LG07)

## CONTEXT FOR CODING AGENT
n8n gave you no unit tests, no diffable logic, and manual Telegram testing. This phase is what makes the rebuild trustworthy. You also asked earlier for a **proper DB seed so every edge case of the bot can be tested** — this file defines it. Build the seed and golden cases as you go, not at the end.

## Test pyramid
| Layer | Tool | Scope |
|---|---|---|
| Unit | pytest | pure policy modules, PII redaction, grounding, codec, fact builders, route() |
| SQL | pytest + testcontainers (pgvector+PostGIS image, migrations from scratch) | each `.sql` file, RLS, idempotency/concurrency |
| Node | pytest-asyncio + fakes | each graph node with fake `DecisionProvider`/LLM/channel |
| Graph (golden) | JSON cases + runner | whole turn → plan, facts, outbound messages, DB side effects |
| Contract | snapshot tests | Pydantic JSON Schemas, channel payload fixtures |
| Chaos | pytest | Jev down, LLM timeout, DB failover mid-turn, worker kill |
| Load | k6/Locust | gateway ack latency, queue throughput, orchestration overhead (fake models) |
| Live eval (weekly) | `csa/evals` | real models on a redacted replay set; Jev vs LLM agreement |

## Golden case format (`tests/golden/cases/*.json`)

### Customer-service evaluation expansion (2026-10-22)

Four `cs_*` cases extend the executable suite to 37 JSON cases (38 pytest cases
including seed isolation/timing). The real worker, graph, spoke runner, SQL,
checkpoint/timeline and persisted outbox remain in use:

- Tenant KB policy retrieval and semantic-check rejection/regeneration of invented
  free return shipping; a foreign policy marker never reaches model prompts/replies.
- Foreign-only KB content produces one local knowledge-gap row, without a foreign
  answer or an unsupported promise of human follow-up.
- Another contact's ticket ID returns `ticket_not_found`, without disclosing its
  subject or changing ticket state/events.
- A persisted human escalation creates one open ticket and acknowledgement; two
  subsequent turns remain muted with no classifiers/planners/composers, duplicate
  escalations, acknowledgements or deliveries.

The test seed supports tenant-separated KB articles and contact-separated tickets.
KB is explicitly registered/enabled only in the disposable synthetic tenant that
needs it; no production/tenant switches change. `TEI_ENDPOINT` is removed from
the golden test process before settings are loaded, and article/query vectors use
the existing `hash-384` deterministic lexical embedder. This is not TEI retrieval
quality acceptance. Cassettes script classification/planning/composition/review,
so this proves orchestration and supplied-verdict handling, not real-model accuracy
or language quality. There is no delivery worker or external provider request.

Final command:
`NO_LOCAL_ENV=1 ENV=ci uv run pytest tests/test_golden.py tests/test_kb_agent.py tests/test_staff_path.py -q`
— **71 passed, 63 existing warnings in 94.12 s**, no skips/deselections.
`make lint` passed Ruff, formatting, Pyright and import boundaries.
Initial new-case selection: **2 failed / 2 passed / 34 deselected, 5 warnings in
24.06 s**, because the old core-only seed lacked KB registration; explicit
synthetic registration fixed it. Focused rerun: **4 passed / 34 deselected,
5 warnings in 21.71 s**. One broader command named nonexistent ticket/escalation
test files and collected no tests; the final command uses their existing
`test_staff_path.py` coverage. Full local/load/reset checks were not rerun per
the user's focused-local/full-hosted-CI policy. No production acceptance implied.

The implementation uses strict Pydantic-validated JSON rather than adding a YAML dependency.
The example below is the original planning notation; executable cases use `seed`, `turns[].cassette`
(response-schema names such as `Classification`, `ComposeOut`, `ReviewOut`) and `turns[].expect`.
`tests/golden/runner.py` seeds a fresh synthetic tenant per case. The real worker, graph, agents,
SQL, checkpoints and timeline run; only model outputs are replayed. Missing cassette responses
fail the test, and expectations include grounded composer input, actual reply, buttons, escalation
and pending outbox counts.
```yaml
id: order_status_one_active_order
seed: demo_client_basic          # seed scenario to load
client: demo-client
channel: telegram
contact: cust_known_1
turns:
  - say: "where is my order"
    decide:                      # cassette: recorded/fake provider answers
      intent: order
      needs: {order_agent: 0.93}
    expect:
      plan: {source: jev, tasks: [{agent: order_agent, input: {action: status, order_id: ORD-A-1001}}]}
      facts_contain: ["ORD-A-1001 is shipped"]
      outbound: {count: 1, render_mode: llm}
      reply_must_include: ["ORD-A-1001"]
      db: {agent_runs: 1, outbound_deliveries: {pending: 1}}
```
Rules: deterministic by default (cassettes + fake LLM). A separate `--live` mode hits real providers and only asserts invariants (grounding passes, no PII, language matches).

## Seed design (`db/seed/`)
Idempotent scripts, one scenario per file, loadable per test. Entities: 2 clients (A: `suggest`, B: `auto`, different persona/policy/timezone/ops chat) to prove tenancy; contacts on each channel; products with edge stock; orders in every status; bookings; tickets; KB docs with gaps.

The local operator seed lives in `sandbox_clients.sql` and
`sandbox_scenarios.sql`. `make db-reset` applies both after migrations, while
`make db-seed` reapplies them to an existing isolated dev database without
dropping schemas. The seed is synthetic and repeatable: two sandbox tenants
with suggest/auto modes, multilingual Telegram contacts, open conversations,
regular/low/zero-stock products, orders across all supported statuses, bookings,
open tickets and unresolved knowledge gaps. Both commands use the Makefile's
loopback dev DSN; `db-seed` also checks that psql and Python point at the same
isolated database before writing. Never point these commands at production.

### Edge-case matrix (each row = at least one golden case)
| Area | Case | Expected |
|---|---|---|
| Identity | unknown sender (first contact) | contact created, `first_contact` ⇒ review, no exception |
| Identity | same Telegram id under client A and B | resolved by credential, never cross-client |
| Identity | wrong contact taps another contact's confirm button | `CONFIRM_REJECTED`, nothing changes |
| Orders | 0 / 1 / many active orders, "where is my order" | none ⇒ fact; one ⇒ status; many ⇒ list + buttons (no id question) |
| Orders | order id typed with/without `ORD-` prefix, lowercase | resolves |
| Orders | other contact's order id | `ORDER_NOT_FOUND` hint, no leak |
| Orders | draft: qty > stock, qty 0, qty 100 (cap 99), unknown SKU | validation/conflict hints |
| Orders | double tap product button | one draft |
| Orders | confirm after expiry / twice / after cancel | rejected / idempotent / rejected |
| Orders | typed "yes/ok/confirm" with pending confirmation | asks to tap Confirm; never confirms |
| Orders | cancel shipped order | refused → refund/ticket path |
| Orders | `ord:buy:*`, `ord:browse` buttons | handled |
| Orders | expiry sweep releases reservation | stock restored, `order_events` row |
| Language | en, ms, Manglish, zh, ta, unsupported (e.g. Thai) | correct reply language; unsupported ⇒ ack + human follow-up fact |
| Safety | prompt injection (obvious and subtle), system-prompt request | blocked/escalated; no leak |
| Safety | "lawyer/sue/chargeback/peguam/律师" | urgent escalation regardless of model |
| Safety | PII in message (NRIC, card, phone, email) | placeholders in logs/LLM; rehydrated to customer |
| Safety | long digit tracking number | not mangled as a card |
| Flow | greeting/thanks/goodbye | no agent, no planner LLM |
| Flow | empty message / sticker / unsupported attachment | clarify or review |
| Flow | voice message ok / transcription failure | normal / escalate |
| Flow | clarify twice | second time escalates |
| Flow | 3 tasks in one message / unknown agent / agent returns bad shape | parallel / escalate / `AGENT_BAD_RESULT` handled |
| Flow | angry customer | handoff + high priority; turn completes (n8n bug #1) |
| Resilience | Jev down / LLM timeout / both | cascade / deterministic fallback; never silence |
| Resilience | agent circuit open | agent hidden from signals; graceful fact |
| Resilience | worker killed mid-turn | resumes, no duplicates |
| Resilience | Telegram 429 / 5xx / blocked-by-user | retry / DLQ / mark contact |
| Resilience | two messages at once from one contact | ordered, serialized |
| Policy | each `never_auto` intent; attachment; partial result | review |
| Policy | client B `auto` vs client A `suggest` same message | different behavior per config |
| Budget | `tokens_per_day` exhausted | degraded template + escalate |
| WhatsApp | inside vs outside 24 h window; STOP keyword; button limits | free-form / template / opted-out / list fallback |
| Gmail | auto-reply / list mail / own address / thread continuation / attachment | ignored / ignored / ignored / correct headers / review |
| Webhook | bad signature, stale timestamp, replay, oversized body | 401 / 401 / deduped / 413 |
| Approval | approve / edit / reject / expire | resumes correctly + audit rows |

### Additional cases from the `11_context` / `22` / `23` review
| Area | Case | Expected |
|---|---|---|
| Escalation | customer escalated, then sends 3 more messages within 24 h | no planner/composer; one acknowledgement per window; human owner notified; conversation stays `escalated` (n8n bug #17) |
| Escalation | mute expires / ops taps "Return to AI" | AI resumes |
| Escalation | ticket creation fails but staff notified / reverse / both fail | `partial` / `partial` / `ESCALATION_FAILED`; promise-of-human only when a fact exists |
| Escalation | second escalation in same conversation | reuses the open escalation ticket |
| Context | customer with a draft and a cancelled order asks "where is my order" | drafts/cancelled not listed as orders (bug #20) |
| Context | outbound reply containing the customer's own email, then next turn | model context shows placeholder, not raw email (bug #21) |
| Context | 3 clarifying turns in a row | second `ask_clarifying` escalates (bug #18) |
| Language | "Can you take this to the manager" | English (not Malay) |
| Language | Thai, Hindi, Indonesian | `other`/unsupported path, escalation fact |
| Language | "ok", "thanks" after a Malay conversation | stays Malay (contact locale prior) |
| Tickets | `#12`, `TKT-12`, `12`; alias actions (`close`, `status`) | normalized |
| Tickets | duplicate create within 10 min | returns existing ticket |
| Tickets | cancel typed "yes" without a button | does not cancel |
| Tickets | cancel via Yes button / expired button / other contact's button | cancels / rejected / rejected |
| Tickets | customer asks for urgent SLA | priority capped to `high` |
| Registry | 5 consecutive retryable failures → circuit opens → probe → closes | per the breaker rules (LG02 §5) |
| Console | `web_console` client_user vs customer | no cross-contact data |

### Additional cases from the voice / notify / expiry / schema review
| Area | Case | Expected |
|---|---|---|
| Voice | transcript contains an email and a phone number | redacted before any model call; raw kept only in `body` |
| Voice | gateway down / returns 5xx at submit | job `failed` at once → escalate path, customer not silent |
| Voice | callback never arrives | reaper fails the job after the deadline → voice-failed fast path |
| Voice | duplicate callback / duplicate Telegram voice update | one ingress event, one reply |
| Voice | callback with bad/missing/stale HMAC | 401, nothing changes |
| Notify | two clients, each with its own targets | an escalation only reaches its own client's targets |
| Notify | one target down, one up | `partial`; dead target raises an alert via the other; no duplicate sends on retry |
| Notify | event not in a target's `events` / below `min_priority` / quiet hours | not sent |
| Expiry | order with two lines of the same product expires | `reserved_qty` decreases by the full sum |
| Expiry | 50 orders for one product expire in one run | `reserved_qty` decreases by the full sum |
| Expiry | customer confirms while the sweep runs | exactly one wins; stock consistent |
| Expiry | unknown order action from the planner | validation error; **no sweep runs** |
| Expiry | `pending_confirmation` order untouched past the staff TTL | staff reminder; no auto-cancel unless configured |
| Expiry | reconciliation after random create/cancel/expire sequences | zero drift |
| Schema | same Telegram user talks to two clients' bots with colliding message ids | both messages stored (endpoint-prefixed keys) |
| Webhook | `reply_mode=sync` answers within `sync_wait_ms`; slower turn | 200 with reply / 202 + callback or poll |
| Webhook | `rate_limit_rpm` exceeded; endpoint `is_enabled=false` | 429 / 403 |
| Outbox | outbound message inserted while the legacy trigger is active | exactly one delivery, with payload |

### Additional cases from the trigger / orders / identity review
| Area | Case | Expected |
|---|---|---|
| Orders | draft with the same SKU twice (A×2 + A×2), 3 available | merged to A×4 → out of stock; nothing reserved |
| Orders | same SKU twice, within stock | one merged line; `reserved_qty` += total |
| Orders | WhatsApp-origin draft | `orders.channel='whatsapp'` accepted; confirmation row carries the channel |
| Orders | forced `orders.id` collision | retried with a new id; turn succeeds |
| Orders | customer cancels a two-line order for one product | full release |
| Escalation | `owned_by='human_support'`, `ai_muted_until` NULL | AI stays muted indefinitely, never auto-released |
| Escalation | ops "Return to AI" (`owned_by=NULL`, `status='open'`) | passes `keep_escalated_status`; AI resumes |
| Webhook | conversation `channel='webhook'`, `callback_url` set | trigger enqueues exactly one payload-less delivery; body built from `messages` |
| Webhook | sync endpoint, caller still connected | sync reply returned; delivery marked `skipped` within the 10 s delay |
| Webhook | sync caller disconnected | callback delivered after the delay |
| Webhook | trigger raises internally (simulated) | gap job creates the missing delivery and alerts |
| Voice | callback replayed 3× | `callback_attempts` increments, one ingress event, one reply |
| Identity | OTP entered wrong 3× | verification `failed`; no identity linked |
| Identity | same phone on WhatsApp and Telegram, unverified | two contacts, no auto-merge |
| RLS | `csa_app` without `app.client_id` / with the wrong one | 0 rows; owner `csa` (n8n) unaffected |

## Replay & regression
- Export redacted real conversations from `ingress_events` + `messages` + `agent_runs` into `tests/replay/` (strip PII with the same redactor; review manually).
- Every production bug becomes a golden case **before** the fix.

## Metrics tracked per release (dashboard or CSV)
Intent accuracy, escalation precision/recall, grounding-failure rate, auto-send rate, approval edit rate (proxy for reply quality), regen rate, p50/p95 latency by stage (ctx, signals, plan, spokes, compose, deliver), fast-path share, cost per turn (tokens + Jev), Jev↔LLM disagreement rate.

## Acceptance criteria
- [ ] `pytest -m "not live"` runs offline in CI in minutes.
- [ ] Every row in the matrix has ≥1 case; coverage report for `csa/graph/policy` ≥ 90%.
- [ ] Seed loads from scratch into an empty test DB in seconds; two clients prove tenant isolation.
- [x] The conversation you hit while testing ("where is my order" asking for an order id) is a passing golden case.
- [ ] Load test report committed: gateway ack p95, queue throughput, orchestration overhead with fake models.

### Verified slice (2026-10-12)

- Seven real-DB golden cases: zero/one/many active orders, draft/cancelled exclusion, semantic
  rejection then regeneration, repeated rejection, checker timeout. The status cases bypass
  the planner and assert no order-id question; multiple orders render tracking buttons.
- 60 synthetic decision labels compared live with Groq and Jev; results and errors are in
  [decision_report.md](../archive/evals/decision_report.md). Offline scoring/provider tests are separate
  from this explicit live CLI.
- `ENV=ci make test`: 285 passed in 8m07s; `make lint`: clean. Reset verified using
  `make db-reset` against a disposable isolated Postgres; two seed tenants, migration 0220.
  Local Gitleaks source scan passed with local secrets/dependencies/caches excluded.
- Entire edge matrix, measured policy coverage, seed timing and full LG08 load-report acceptance
  remain open. WhatsApp/Gmail/voice/budget cases depend on their planned implementations.

### Further evidence (2026-10-12)

- Golden suite expanded to 13 cases: tenant auto/suggest/shadow behavior, first-contact review,
  injection followed by three muted turns, and classifier timeout. Pending approvals and actual
  persisted agent runs are now checked alongside delivery counts.
- `make coverage`: 78 passed, two timing tests excluded; **100% statement/branch coverage**
  across `csa/graph/policy` (153 statements, 78 branches). CI enforces a 90% floor.
- [Load and coverage report](../archive/evals/lg08_validation_report.md): 150 real-SQL ingress requests
  at 50/s; steady-state ack p95 109.642 ms, scheduled ack p95 112.704 ms, 150 completed durable
  turns and unique replies. Cold pool expansion failed the latency target; worker backlog
  throughput measured 2.051 turns/s, not 50 turns/s. No socket/proxy/provider delivery test is
  implied. The report is persisted, but cannot be committed in this non-Git checkout.
- The complete matrix and seed timing remain open. The matrix/coverage acceptance checkbox
  remains unchecked because 100% policy coverage does not prove every matrix row has a case.
- Final `ENV=ci make lint test`: **305 passed** (10m15s), lint/types/import boundary clean;
  frozen dependency sync and disposable `make db-reset` passed. Live dev data was preserved.

### Startup/callback follow-up

- Gateway pre-opens its existing eight-connection pool and cleans up even on startup failure.
  Load tests now run the actual lifespan with a disposable database: first-burst ack p95
  **139.190 ms**, scheduled ack p95 **144.328 ms**, startup **346.742 ms**.
  The earlier cold-expansion failures remain in the report rather than being removed.
- Golden cases now total **17**: repeated order Confirm/Cancel, ticket button cancellation/repeat
  and duplicate ticket creation; persisted statuses, reservations and ticket audit counts verified.
- Focused suite: 24 passed. `make coverage`: 82 passed, two timing tests excluded,
  **100%** policy statement/branch coverage retained.
- Full `ENV=ci make lint test`: **311 passed** (10m30s); disposable `make db-reset` passed,
  schema 0220/two seeded tenants. No production or live dev data was reset.

### Ownership/expiry and real-socket follow-up

- **22** whole-turn golden cases now include wrong-contact order confirmation, expired
  order/ticket callbacks, foreign order lookup and partial-result review. Existing invalid/expired
  callback handling escalates; a dedicated rejection acknowledgement is not implemented.
- Two suggest/auto synthetic tenant scenarios inserted in **0.238 s**, after migrations.
  App-role RLS sees only its own product/order rows. This does not yet measure the entire
  empty-database seed matrix, so the broad seed acceptance remains open.
- A separate actual Uvicorn gateway subprocess, on an ephemeral loopback socket with disposable
  database URLs and runtime tracing, passed 150 arrivals at 50/s: ack p95 **140.408 ms**,
  scheduled p95 **144.002 ms**, 150 completed turns/unique replies/pending deliveries.
- Tracing is batched/compact and retains request spans; contact resolution reduces SQL round
  trips; gateway-only autocommit removes implicit transactions for endpoint reads. Explicit
  tenant transactions and eight-connection capacity are preserved. Focused safety/TCP run:
  **25 passed**. See the [report](../archive/evals/lg08_validation_report.md) for failed attempts and limits.
- Backlog drain remains about two turns/s, after ingress, without external providers/delivery.
  Full matrix and sustained simultaneous ingress/worker/proxy evidence remain open.
- Earlier lint/full suite, before the additional process-level worker tests: **325 passed**
  (12m16s); coverage: **88 passed**, 100% policy coverage.
  Disposable reset and local source scan passed. Unchanged `make load-test` repetition passed
  all three checks: TCP ack/scheduled p95 **108.808/110.330 ms**, ASGI **187.828/190.964 ms**,
  graph-only **97.839 ms**. A preceding post-fix ASGI scheduling failure remains documented.
  Only the dev gateway restarted; readiness is verified on 0220 and live dev data was preserved.
- The additional simultaneous-ingress diagnostic (4 independent test-only runtimes/savers)
  completed 150 turns but reached only about **2.34 turns/s**; latest ingress ack p95 was
  **782.528 ms** under DB contention. Checkpointer work accounts for about 31 calls per turn.
  All 4 load tests pass, but this capacity diagnostic intentionally reports rather than asserts
  the overloaded latency. This benchmark topology is not deployed. 50 turns/s sustained is not
  demonstrated; choosing whether to prioritize cloud autoscaling/database sizing or a
  checkpoint-frequency/durability redesign is an open architectural decision.
- With the process-level worker tests included, `ENV=ci make lint test` passed **328 tests**
  (10m43s); `make coverage` passed **88 tests** with 100% policy statement/branch coverage.
  Two actual spawned processes preserved same-conversation ordering and unique outbox effects;
  after one was terminated while holding its advisory lock, the stale lease was reaped and a
  replacement process completed the event exactly once. Disposable reset and Gitleaks passed.
- **33 whole-turn golden cases** (expanded from 22): added edge-matrix cases covering:
  hard legal keyword escalation (`hard_escalate_legal.json`), angry customer sentiment escalation
  (`safety_angry_customer_escalates.json`), explicit human agent handoff (`safety_wants_human_escalates.json`),
  unknown SKU product rejection (`order_draft_unknown_sku.json`), duplicate SKU line merging
  (`order_draft_duplicate_sku_merged.json`), duplicate SKU line exceeding stock (`order_draft_duplicate_sku_out_of_stock.json`),
  multi-line order reservation release on cancellation (`order_cancel_two_lines_full_release.json`),
  typed confirmation button enforcement (`order_typed_confirm_asks_for_tap.json`),
  repeated clarification escalation bound (`flow_clarify_twice_escalates.json`),
  fast-path greeting routing (`flow_greeting_no_agent_no_plan.json`),
  and tenant policy `never_auto` review hold (`policy_never_auto_complaint.json`).
  All 33 cases pass against real PostgreSQL testcontainers and worker runtime.

## Pitfalls
- Don't test prompts by eyeballing Telegram; test invariants (grounding, language, no PII, policy) automatically.
- Cassettes rot when models change — keep a small live suite and re-record deliberately.
