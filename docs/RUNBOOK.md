# Runbook (dev stack)

Never point any of this at production databases, credentials, bots, services, or hostnames.
The development environment may share a physical host with production; verify the private dev
configuration and use only the isolated dev stack.

For the latest guarded console runtime and recovery constraints, start with the
[runtime handoff](./handoffs/2026-10-16-runtime-stabilization.md). The generic
first-time commands below are not instructions to reset or reprovision the recovered dev.

### Dependency and database-image provenance

Exact dependency versions are maintained in [pyproject.toml](../pyproject.toml) and
[uv.lock](../uv.lock), not a duplicated version table. The combined PostGIS/pgvector
image is built from [deploy/db.Dockerfile](../deploy/db.Dockerfile).
Historical LG01 publication recorded
`ghcr.io/amsyrzmi/csa-pg16-postgis-vector` at
`sha256:a61cb35736e5792140f139827bc86c698d17675f617cb8c46218e15e5085b223`.
That is provenance, not proof of current registry availability or current deployment
pins: inspect the relevant Compose/CI manifests and verify before deployment.
The old production stock image with separately installed PostGIS was not reproducible;
any production image migration requires a separate approved deployment plan.

## What runs

| Thing | Where |
|---|---|
| Dev Postgres `csa_dev_db` | Isolated Docker network, loopback-only configured dev port |
| Gateway (uvicorn) | 127.0.0.1:18080, `/readyz`, `/hooks/<public_id>` |
| Worker | processes `ingress_events` through the coordinator graph |
| Delivery | sends outbox rows and staff notifications to Telegram |
| Caddy (host) | Dedicated dev hostname block → 127.0.0.1:18080 |
| cloudflared (container) | tunnels the private dev hostname to Caddy |

## First-time setup

```sh
uv sync --frozen
cp .env.example .env.local     # fill in values yourself; never commit
make db-image && make up
make db-reset
make setup-dev-telegram        # prints the webhook public id
```

Optional in `.env.local`: `CONSOLE_API_TOKEN` (≥32 chars, enables `/console/v1`), `TURN_ENGINE=coordinator`, `GROQ_API_KEY`, `GOOGLE_AI_API_KEY` (enables Gemini compose failover), `DEV_STAFF_CHAT_ID`
(staff group chat id; seeded with demo products A1/B2 by `setup-dev-telegram`).

Register the webhook (URL `https://<DEV_HOSTNAME>/hooks/<public_id>`, with the secret token from
`.env.local`) via Telegram `setWebhook`. Keep the actual hostname private to the local environment.

## Daily use

```sh
make up
make run-dev > /tmp/run-dev.log 2>&1 &
curl localhost:18080/readyz       # {"status":"ready"}
```

Restart: use `make restart-gateway` to cleanly restart just the gateway, or `make stop-gateway`
to free port 18080 before re-running `make run-dev`. To stop everything, list PIDs with
`ps -eo pid,cmd | grep -E "apps\.(gateway|worker|delivery)"`, `kill` those PIDs,
then start `make run-dev` again. Stop Postgres with `make down`.

Apply a new migration without resetting:
`set -a; . ./.env.local; set +a; MIGRATE_DATABASE_URL="$LG_DATABASE_URL" uv run python -m scripts.migrate`

## Checks

`make lint` (Ruff, format, Pyright, import-linter) and `ENV=ci make test` (~14 min; latest
full run: 333 passed, including composer failover, process-level worker and simultaneous-ingress
diagnostics).
Run tests without `.env.local` exported into the shell, or settings tests fail.

Focused offline regression run (Docker required, isolated testcontainers; no provider calls):

```sh
ENV=ci uv run pytest -q tests/test_golden.py tests/test_decide_providers.py tests/test_decision_evals.py
```

Policy coverage: `make coverage` enforces at least 90% with branch instrumentation.
Synthetic load: `make load-test` exercises real ingress SQL and durable queue/outbox in disposable
testcontainers (no external provider or Telegram calls). Do not run timing tests under coverage
or alongside builds. See the [LG08 validation report](./evals/lg08_validation_report.md) for
steady-state results, cold-pool failures, queue throughput and measurement limitations.

The gateway now opens all eight existing pool connections before accepting traffic. Startup
fails explicitly if those connections cannot open; pool cleanup also runs on failure. Allow
eight database connections per gateway process (worker/delivery sizing is unchanged).
The load test uses the actual gateway lifespan and measures the first burst after startup:
it now also starts a separate Uvicorn gateway on an ephemeral loopback port. That real TCP
path, with logging/tracing enabled, passed p95 140 ms (scheduled p95 144 ms); this is not a
public-hostname or sustained-worker SLA. The ASGI comparison stubs logging/tracing.
Unchanged repetition passed TCP p95 109 ms and all three load checks; a preceding ASGI
shared-loop scheduling failure remains recorded in the report. Run on an otherwise quiet
dev/test host and retain failures; do not loosen thresholds or treat these bursts as cloud sizing.
An additional concurrent-worker diagnostic completes 150 events but currently drains only
about 2.3 turns/s and misses the 200 ms acknowledgement p95 while the DB is busy. It is
diagnostic—not a capacity pass—and the current worker entrypoint remains serial. Do not scale
worker concurrency or relax checkpoint semantics based on this small-host run alone; see the
report for measurements and the outstanding capacity decision.

Two-process echo-worker integration tests now cover ordered claims, unique replies/deliveries,
advisory-lock release on OS process termination and stale-lease recovery. They do not prove
multi-process coordinator/checkpoint throughput or a sustained capacity target.

### Worker-capacity planning

The current `apps.worker.main` runs one queue consumer per process. To scale in a cloud
deployment, add worker **process replicas** against the same database; the ordered claim query
and per-conversation session advisory lock are designed to coordinate multiple processes.
Each process has its own synchronous Postgres checkpointer, so checkpoints remain durable at
each graph step. Do not share one `AsyncPostgresSaver` instance across independent consumers:
its internal lock serializes checkpoint calls.

Budget connections before changing replica count. The configured worker maxima are four
owner-role claim/reaper connections, eight application-role connections, four application-role
checkpoint connections, and one extra owner-role advisory-lock connection while the current
single consumer is processing a turn: up to **17 connections per process** across roles.
Pools may be lazy-opened, but the PostgreSQL connection limit must cover maximums plus gateway,
delivery, migrations/admin and operational headroom. Increasing replicas is not expected to
increase throughput linearly once Postgres, checkpoint I/O, storage or external model limits
saturate.

Observe queue backlog and turn service rate before autoscaling. A low-sensitivity backlog
snapshot (owner connection, read-only; use synthetic/dev data during development):

```sql
SELECT client_id, status, count(*) AS events,
       round(extract(epoch FROM now() - min(created_at))::numeric, 1) AS oldest_age_seconds
FROM public.ingress_events
WHERE status IN ('received', 'dispatched', 'failed')
GROUP BY client_id, status
ORDER BY oldest_age_seconds DESC NULLS LAST;
```

Track incoming acknowledgements separately from completed turns. Scale workers only while
oldest queue age grows and the database remains below a load-tested connection/CPU/I/O budget;
scale or tune Postgres before adding replicas when checkpoint latency rises. Set alert thresholds
from production SLOs and measured capacity, not the local two-turn/s diagnostic. The current
test excludes external model latency, simultaneous delivery, Caddy/Cloudflare and a sustained
cloud-sized workload; these are required before claiming a production capacity target.

Only the gateway pool uses autocommit for unscoped endpoint lookup; tenant operations still
run inside explicit transactions. Worker/delivery pool defaults are unchanged. Console tracing
now exports compact JSON lines in batches, so output may appear after a short delay rather than
immediately. FastAPI retains request spans and omits per-chunk receive/send spans. Tests that
need spans immediately must call the returned provider's `force_flush()`.

## Jev and semantic checking

Enter `TYPESAFE_API_KEY` yourself in the ignored `.env.local`; never print it. The worker uses it
only when a tenant's `clients.config.policy.decide_mode` selects `shadow`, `cascade` or `jev`.
The default is `llm`; the local dev tenant currently uses `cascade`. Restart the worker after
changing an API key. Policy changes are read on each turn.

- `shadow`: Groq decides; Jev runs alongside for comparison.
- `cascade`: Jev decides at or above `jev_min_confidence` (default 0.5); errors or lower
  intent confidence explicitly fall back to Groq.
- `jev`: errors fail the node; there is no classifier fallback.
- `decision_log` stores the model version, typed outputs, disagreement and fallback reason,
  never customer text. Check it with tenant-scoped access.
- `semantic_verify` defaults to true: Groq checks claims after deterministic grounding.
  Rejection gets one regeneration; checker errors do not silently pass.

Optional live evaluation (sends **synthetic** case text to the configured external providers):

```sh
ENV=dev uv run python -m csa.evals.decisions --provider both --concurrency 1 --out docs/evals/decision_report.md
```

See [decision_report.md](./evals/decision_report.md) for the measured comparison. Review Jev
retention and operational terms before real-client rollout. This evaluation does not calibrate
all decision thresholds or prove production throughput.

## Gotchas

- `make db-reset` is destructive: it regenerates the webhook id, so rerun `make setup-dev-telegram` and
  re-register the webhook, otherwise Telegram gets 404.
- Something else already on port 18080 (an old dev stack) blocks the gateway; kill it by PID.
- Escalation mutes the AI for 24 h. Tap **Resume AI** in the staff alert, or in dev:
  `UPDATE conversations SET ai_muted_until=NULL`.
- Reload Caddy only after review; never edit existing blocks.

## Try it

Message the dev bot "I want 2 A1" → draft order with Confirm/Cancel buttons → Confirm → staff group alert →
Approve/Reject → customer notified. "I want to talk to a person" → escalation alert with Resume AI.
