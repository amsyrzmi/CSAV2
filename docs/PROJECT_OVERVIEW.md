# Project overview: what each file does

See [ARCHITECTURE.md](./ARCHITECTURE.md), [RUNBOOK.md](./RUNBOOK.md), [PROGRESS.md](./PROGRESS.md).

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
| `csa/console/` | Timeline queries and the read-only `/console/v1` router ([CONSOLE_API.md](./CONSOLE_API.md)) |
| `csa/approval/service.py` | Hold AI replies for staff review; decide and expire them |
| `csa/graph/policy/gate.py` | Pure send/hold/suppress policy |
| `csa/staff/callbacks.py` | Handles staff approve / reject / resume taps |
| `csa/contracts/` | Pydantic contracts: envelope, plan, state, callbacks, approval, registry |
| `csa/core/` | Settings, DB pool, crypto, logging, PII, tenancy, tracing, clock |
| `db/migrations/` | Ordered additive migrations (current 0220: tenant-scoped `decision_log`) |
| `db/sql/` | All parameterised SQL, by area |
| `db/seed/` | Persistent sandbox tenants |
| `scripts/` | `migrate.py`, `setup_dev_telegram.py` (dev tenant, endpoint, demo products, staff target), `lg_setup.py` |
| `tests/` | Pytest suite (333 passed on 2026-10-13); `golden/` has 22 real-DB whole-turn cases; `test_policy_edges.py` policy boundaries; `test_load_acceptance.py` ASGI/TCP ingress and simultaneous-worker benchmarks; `test_worker_multiprocess.py` process ordering and crash recovery |
| `Makefile` | Dev lifecycle, lint/tests; `coverage` enforces 90% policy branch coverage, `load-test` reproduces isolated benchmarks |
| `docs/langgraph-rebuild/` | LG00–LG13 phase plans and acceptance criteria |
| `AGENTS.md`, `SERVER_HANDOFF.md` | Repo rules and server facts |
| `OriginalArchitecture.md`, `web_console.md` | Legacy design and console notes |
