# Project overview: what each file does

See [ARCHITECTURE.md](./ARCHITECTURE.md), [RUNBOOK.md](./RUNBOOK.md), [PROGRESS.md](./PROGRESS.md).
Current continuation: [M4 closeout and M5 kickoff](./handoffs/2026-10-16-m4-closeout-m5-start.md).
M4 is functionally closed with accepted laptop-dev performance debt; M5 is not implemented.

| Path | Role |
|---|---|
| `apps/gateway/main.py` | FastAPI webhook gateway, health, schema-version check |
| `apps/worker/main.py` | Claims ingress events, runs the turn engine |
| `apps/delivery/main.py` | Sends outbox messages (renders buttons), runs staff sender |
| `apps/telegram_poller/` | Polling alternative to webhooks |
| `csa/channels/` | `base.py` channel contracts, `telegram.py` adapter, `ingress.py` webhook handling and button clearing |
| `csa/turn/` | `deps.py` wires DB, LLM and agents into the graph |
| `csa/graph/` | `coordinator.py` graph (plan → spokes → bounded observe loop → gate), `policy/` routing, plan and handoff rules, `saver.py` checkpoints, `retention.py` |
| `csa/decide/` | `groq.py` client (retry), `llm.py` classify/plan/next_step/compose/semantic review, `jev.py` typed classifier, `providers.py` shadow/cascade selection, `grounding.py` fact checking |
| `csa/evals/` | Offline scoring helpers, 60 synthetic decision cases, explicit live LLM/Jev comparison CLI |
| `csa/agents/` | Spokes: `orders`, `order_approval`, `escalation`, `tickets`; `runner`, `breaker`, `recorder` |
| `csa/notify/` | `notifier.py` queues staff alerts; `sender.py` delivers them with action buttons |
| `csa/graph/events.py` | Redacted per-step timeline events written by the graph |
| `csa/console/` | Session identity/RBAC, onboarding, audited capability/KB administration, runtime reads and private read-only-business sandbox chat ([CONSOLE_API.md](./CONSOLE_API.md)) |
| `csa/approval/service.py` | Hold AI replies for staff review; decide and expire them |
| `csa/graph/policy/gate.py` | Pure send/hold/suppress policy |
| `csa/staff/callbacks.py` | Handles staff approve / reject / resume taps |
| `csa/contracts/` | Pydantic contracts: envelope, plan, state, callbacks, approval, registry |
| `csa/core/` | Settings, DB pool, crypto, logging, PII, tenancy, tracing, clock |
| `db/migrations/` | Ordered additive migrations through 0290 (sandbox chat); 0270 identity, 0280 KB uploads; staged tenant RLS |
| `db/sql/` | All parameterised SQL, by area |
| `db/seed/` | Persistent sandbox tenants |
| `scripts/` | `migrate.py`, `setup_dev_telegram.py` (dev tenant, endpoint, demo products, staff target), `lg_setup.py` |
| `tests/` | Pytest synthetic unit/integration/golden suites; `test_policy_edges.py` policy boundaries; `test_load_acceptance.py` ASGI/TCP ingress and simultaneous-worker benchmarks; `test_worker_multiprocess.py` ordering/crash recovery. Exact current results and accepted performance debt live in [PROGRESS.md](./PROGRESS.md), not a duplicated stale count. |
| `Makefile` | Dev lifecycle, lint/tests; `coverage` enforces 90% policy branch coverage, `load-test` reproduces isolated benchmarks |
| `docs/handoffs/` | Current M4 closeout/M5 prompt, source migration plan and retained recovery/agent-preparation history |
| `docs/langgraph-rebuild/` | LG00–LG15 phase plans and acceptance criteria |
| `AGENTS.md` | Repository rules and isolated-dev safety |
