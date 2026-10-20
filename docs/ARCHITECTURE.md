# Architecture

CSA is a multi-tenant customer-service runtime: Python 3.12, FastAPI, LangGraph, PostgreSQL (psycopg 3 async).
Phase plans live in [langgraph-rebuild/](./langgraph-rebuild/LG00_master_plan.md).
Current release/domain boundaries are in the
[domain/release handoff](./handoffs/2026-10-19-domain-release-next-session.md).
This describes application structure, not proof of running services or public deployment.

## Request pipeline

```
Telegram ─► Cloudflare edge ─► cloudflared ─► Caddy ─► gateway /hooks/<public_id>
                                      │  verify secret, parse, dedupe
                                      ▼
                               ingress_events (Postgres)
                                      ▼
                  worker: claim row, per-conversation advisory lock
                                      ▼
                       coordinator graph (LangGraph)
                                      ▼
                    messages + outbound_deliveries (outbox)
                                      ▼
                  delivery worker ─► Telegram sendMessage (+ buttons)
```

Staff path: an agent calls `notify()` → `staff_notifications` → `StaffNotificationSender`
(runs inside the delivery process) → staff group with `s:<uuid>` buttons → tap hits the gateway →
`handle_staff_action`.

Customer buttons use `a:<uuid>` tokens stored by `PendingActionStore`. Order Confirm/Cancel are
button-only; the LLM can never confirm an order.

## Coordinator graph (`csa/graph/coordinator.py`)

`load_ctx → guard → signals → route → plan → spokes → collect ⇄ observe → compose → verify → gate → emit → finalize`

- **Agent loop** (`observe`): on LLM-planned turns, after the agents return the model looks at the facts
  and may request up to 2 more rounds of follow-up lookups (`LLMProvider.next_step`). Code enforces the
  limits: only `read` actions, no repeats, at most 2 tasks per round, 6 tasks and 3 rounds per turn (hard
  ceilings), the tenant's `policy.agent_loop_max_steps` (default 2, 0 disables). The loop stops when the
  customer must confirm or choose, or a human is needed. A failed observe ends the loop and the reply still
  uses the facts found so far.
- **Risk classes**: every agent action declares `read` (lookup), `draft` (needs the customer's button tap)
  or `commit` (changes business state) via `Agent.risk()` / `ACTION_RISK`. The first plan may draft; only
  button taps and staff actions commit; the loop may only read.

- **signals** selects a classifier through `csa/decide/providers.py`: `llm` (default),
  `shadow` (both; LLM wins), `cascade` (Jev first; explicit Groq fallback on error or low intent
  confidence), or `jev` (no fallback). Non-LLM modes write typed results, concrete model version,
  disagreements and fallback reason to tenant-scoped `decision_log` (migration 0220); no message
  text is stored there. The signals node budget is 8 s (Jev timeout 2 s, Groq budget 5 s).
  Jev does not compose replies or extract action arguments.
- **plan/compose** use the Groq LLM (`csa/decide/`), with node budgets 8 s / 10 s and one retry.
- **Order context** loads only this contact's active, placed orders. Draft, cancelled, delivered
  and refunded orders are excluded from the generic status list. "Where is my order" resolves
  deterministically: zero gives a fact, one looks up its status, many list statuses and tracking
  buttons. Explicit draft-id lookups remain supported.
- **route** precedence (`graph/policy/routing.py`): muted, callback, empty, voice failed, hard-escalate regex,
  guard blocked, unsupported language, injection, no signals → clarify, chitchat / wants_human / angry →
  escalate, capability score > 0 → plan args, else clarify.
- **verify** first checks numbers/URLs deterministically, then uses Groq to check business claims
  against facts and failures (`policy.semantic_verify`, default true). Rejection reasons guide one
  regeneration; another rejection or a checker error takes the persisted fail-safe path. Callbacks
  with verbatim acknowledgements and deterministic handoff replies bypass semantic checking.
  The timeline exposes only the issue count, not free-text checker output.
- **gate** (`graph/policy/gate.py`) reads `clients.config.policy` and returns send / hold / suppress:
  `{"deployment_mode": "shadow|suggest|auto", "never_auto": ["refund_returns"],
  "always_review_if": ["first_contact","partial_result","requires_human","email_channel"],
  "auto_send": {"default": true, "by_intent": {"product_info": 0.7}}, "approval_ttl_minutes": 30,
  "agent_loop_max_steps": 2, "semantic_verify": true,
  "decide_mode": "llm", "jev_min_confidence": 0.5}`.
  Held replies go to the `hold` node: an `approvals` row plus a staff alert with **Send reply / Reject**
  (`csa/approval/service.py`). Approve sends once; reject or expiry sends the safe fallback.
- Callback taps skip LLM classification.
- A muted conversation emits nothing and goes straight to `finalize`.

## Visibility (console)

Each graph node appends a redacted row to `agent_events` (node, agent, status, duration, codes/counts only).
A failed write is logged loudly but never fails the customer turn. The console reads them through
`/console/v1` ([CONSOLE_API.md](./CONSOLE_API.md)). Every future agent shows up automatically.

## Evaluation and regression evidence

- `csa/evals/decisions.py` compares live classifiers against 60 synthetic labelled messages;
  [decision_report.md](./evals/decision_report.md) records the measured results, not an SLA.
- `tests/golden/` uses JSON cases and schema-keyed model cassettes with real Postgres, agents,
  worker, graph, timeline and outbox. Twenty-two cases cover order-status resolution, semantic
  rejection/regeneration/failure, tenant review/suppression, injection/mute, classifier timeout
  order/ticket callback repetition, wrong-contact/expired tokens and multi-agent partial results;
  they do not yet cover the entire LG08 matrix.
- Policy coverage is 100% with branch instrumentation; `make coverage` and CI enforce a 90% floor.
  [LG08 validation report](./evals/lg08_validation_report.md) distinguishes measured steady-state
  ingress latency from cold-start failures and durable backlog throughput; neither is a cloud SLA.
- The dev tenant uses `cascade`. Other tenants retain the `llm` default. Jev vendor retention,
  regional hosting and operational guarantees still require review before real-client rollout.

## Spoke agents (`csa/agents/`)

| Agent | Purpose |
|---|---|
| `orders` | Draft an order from catalogue products; customer must tap Confirm |
| `order_approval` | Staff approve / reject a confirmed order |
| `escalation` | Mute the AI for 24 h, alert staff with a **Resume AI** button |
| `tickets` | Create support tickets |

`SpokeRunner` runs them with a circuit breaker and records every run. Planned agents are in LG13.

## Data model & safety

- Every tenant table has `client_id` and staged Row-Level Security (do not `FORCE` while legacy n8n uses the owner role).
- All SQL is in `db/sql/` and parameterised. Migrations are ordered and additive (`db/migrations/`).
  Gateway refuses to start if the schema version differs from `EXPECTED_SCHEMA_VERSION`.
- Secrets are never logged; Groq error codes are secret-free (`groq_timeout`, `groq_http_<status>`…).
- Only `csa/graph/` imports LangGraph (import-linter enforced).
- Gateway startup pre-opens its existing eight-connection pool before accepting requests;
  pool-open failures propagate and cleanup runs. Worker/delivery pool defaults are unchanged.
  Budget eight app-role connections per gateway process when sizing replicas.
- Gateway endpoint lookup uses autocommit; tenant reads/writes still use explicit `tx()`.
  Contact resolution consolidates contact/identity queries, retaining the existing fallback
  conflict checks for concurrent inserts and legacy identities.
- OpenTelemetry export is batched; console output is compact JSON lines. FastAPI records
  server request spans, not per-chunk receive/send children. Agent timeline events are unchanged.
- Worker execution is currently one sequential consumer per process. Multiple process replicas
  can claim different conversations; session advisory locks plus the ordered SQL claim preserve
  per-conversation ordering. Each process has its own synchronous checkpointer; do not weaken
  per-node checkpoint durability to infer capacity from the short test. The synthetic four-lane
  diagnostic completed about 2.34 turns/s and missed 200 ms ingress p95 under database contention.
  This is not cloud capacity evidence. Size Postgres and worker replicas from queue-age,
  checkpoint-latency, connection, model and delivery measurements ([runbook](./RUNBOOK.md)).
  Process-level echo-worker tests verify two-process ordering/idempotency and lease recovery
  after OS process termination; coordinator throughput and crash-after-side-effect behavior need
  separate evidence.
- Staff authorisation is by chat id of the configured target (any member of the staff group can act).
- Dev and production are fully isolated: dev Postgres `csa_dev_db` on its configured dev port,
  gateway on 18080, dedicated dev hostname from the private environment configuration, separate
  dev bot.
