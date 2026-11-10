# Runbook (dev stack)

Never point any of this at production databases, credentials, bots, services, or hostnames.
The development environment may share a physical host with production; verify the private dev
configuration and use only the isolated dev stack.

For the latest guarded console runtime and recovery constraints, start with the
[domain/release handoff](archive/handoffs/2026-10-19-domain-release-next-session.md) and retained
[runtime history](archive/handoffs/2026-10-16-runtime-stabilization.md). The generic
first-time commands below are not instructions to reset or reprovision the recovered dev.
Repository-only CI/image packaging is implemented; see the
[release contract and inert staging proposal](../deploy/RELEASE.md). Public hosting,
promotion and deployment remain disabled/unverified. The historical production Compose
file still assumes the old database/network and a conflicting host port; do not run it
for this release. Preserve the existing `fourstacks.my`/`www` landing service.

### Dependency and database-image provenance

Exact dependency versions are maintained in [pyproject.toml](../pyproject.toml) and
[uv.lock](../uv.lock), not a duplicated version table. The combined PostGIS/pgvector
image is built from [deploy/db.Dockerfile](../deploy/db.Dockerfile).
Historical LG01 publication recorded
`ghcr.io/amsyrzmi/csa-pg16-postgis-vector` at
`sha256:a61cb35736e5792140f139827bc86c698d17675f617cb8c46218e15e5085b223`.
That digest was superseded in the current CI and staging-proposal source on
2026-10-20 by registry index digest
`sha256:4e610cbe04be850136f42fffcc1ef546ceff5b13d8f25322738d0d74a9a1fefe`.
This records repository configuration, not current registry availability or a
deployed image. Inspect the relevant Compose/CI manifests and verify before deployment.
The old production stock image with separately installed PostGIS was not reproducible;
any production image migration requires a separate approved deployment plan.

## Reference topology (not a live host inventory)

The table describes the isolated development layout the runbook supports. It does
not assert that any host service, tunnel, database or hostname is currently
running. Confirm state only under a separately authorized, isolated operation.

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

`NO_LOCAL_ENV=1 ENV=ci make lint` runs Ruff, format, Pyright and import-linter;
`NO_LOCAL_ENV=1 ENV=ci make test` runs the suite. Runtime and counts vary: the old
333-test/~14-minute figure was historical, not a current estimate. Consult
[progress](./PROGRESS.md) for exact focused/full-suite results and latency limitations.
Run tests without `.env.local` exported into the shell, or settings tests fail.

Focused offline regression run (Docker required, isolated testcontainers; no provider calls):

```sh
ENV=ci uv run pytest -q tests/test_golden.py tests/test_decide_providers.py tests/test_decision_evals.py
```

Policy coverage: `make coverage` enforces at least 90% with branch instrumentation.
Synthetic load: `make load-test` exercises real ingress SQL and durable queue/outbox in disposable
testcontainers (no external provider or Telegram calls). Do not run timing tests under coverage
or alongside builds. See the [LG08 validation report](archive/evals/lg08_validation_report.md) for
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
for the customer classifier when a tenant's `clients.config.policy.decide_mode` selects `shadow`,
`cascade` or `jev`, or when the tenant has stored no `decide_mode` and
`CUSTOMER_DECIDE_MODE_DEFAULT=cascade` (see "Jev for the staff assistant" below). The default is
`llm`; the local dev tenant currently uses `cascade`. Restart the worker after changing an API
key. Policy changes are read on each turn.

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

See [decision_report.md](archive/evals/decision_report.md) for the measured comparison. Review Jev
retention and operational terms before real-client rollout. This evaluation does not calibrate
all decision thresholds or prove production throughput.

## Jev for the staff assistant and the customer classifier

One `TYPESAFE_API_KEY` serves two places: the gateway (the staff assistant's quick typed
decisions) and the worker (the customer-message classifier). Jev only answers typed questions; it
never writes text, never decides a send, and sees only redacted text (addresses, phone and card
numbers, and the recipient names the caller knows). With no key every area runs on the LLM or the
server rule and nothing errors. A key of `disabled`, blank, or the staging disabled-model-key text
reads as no key.

**Switches** (gateway environment unless noted; the first three are per area):

| Setting | Values | Meaning |
| --- | --- | --- |
| `ASSISTANT_DECIDER_EMAIL` | `auto`, `llm`, `shadow`, `cascade` | Batch email: shared or personalised, which facts to read, whether the words ask to send. |
| `ASSISTANT_DECIDER_ROUTING` | same | Which facts (manager, mailbox, team, training, People) reach the planner. |
| `ASSISTANT_DECIDER_INBOUND_INJECTION` | same | Refuses to draft a reply to an inbound email that tries to steer the assistant. |
| `ASSISTANT_JEV_FLOOR_NOUL`, `ASSISTANT_JEV_FLOOR_CHOICE` | 0 to 1, default 0.7 | Jev answers a question only at or above its floor; below it the LLM or rule answers. |
| `ASSISTANT_DECIDER_AUDIT_SAMPLE` | 0 to 1, default 0.10 | Share of confident Jev answers also answered by the fallback, to log drift. The injection check always does. |
| `CUSTOMER_DECIDE_MODE_DEFAULT` (worker) | `llm` (default), `cascade` | The customer classifier's mode for a company with no `decide_mode` stored, and only while a key exists. A stored mode, an explicit `llm` included, always wins. |

`auto` is `cascade` with a key and `llm` without one; `shadow` runs Jev beside the fallback and logs
both; `cascade` lets Jev decide each question at or above its floor. An explicit `shadow` or
`cascade` without a key refuses to start. A Jev error, timeout (2 s) or malformed answer falls back
and never reaches the user.

**Kill switch.** Per area, set it to `llm` and restart the gateway: `ASSISTANT_DECIDER_EMAIL=llm`
(and the other two). For everything at once, remove the key (below). For the customer classifier,
set `CUSTOMER_DECIDE_MODE_DEFAULT=llm` and restart the worker, or force one company with
`csa.ops.runbooks.force_decide_mode(conn, client_id, "llm")` (runbook 04): that stored mode wins
over the default.

**Rollout order** (each step its own reviewed change; the owner does the key steps):

1. Roll out the images and migration 0542 with no key wired into the services. Every area runs on
   the LLM or rule. (A rollout with no key plans and applies cleanly: the staging compose files map
   the disabled placeholder to `TYPESAFE_API_KEY`, and the real key's file is not asked for.)
2. Run the evaluation from your own shell, passing the key there and never from a repo file:
   `TYPESAFE_API_KEY=... python -m scripts.assistant_jev_eval`. It prints, per area and question,
   agreement with the synthetic English and Malay labels on the answers at or above the floor,
   the coverage, and whether the question meets its target (90 percent; 95 for the injection
   check). Exit 0 passes, 1 means a question is below target (raise its floor, or set that area to
   `llm`, then run again), 2 means it could not start. It prints counts and case ids, never
   message text. The customer classifier has its own check:
   `python -m csa.evals.decisions --provider both`. Put the result in the PR.
3. Only after it passes, create the key file and wire it. On staging: write
   `/srv/csa-staging/secrets/pipeline/staging_jev_key` (owner UID 1000, mode 0400, the key and no
   newline), then a reviewed PR adds `CSA_STAGING_JEV_SECRET=staging_jev_key` to
   `deploy/staging-rollout.env` (the file allows it; the line is optional), then roll out with the
   `staging-rollout` skill: `plan` stops (exit 2) until the file exists. That turns the staff areas
   on, because `auto` becomes `cascade`, in the gateway; the worker has the key too but its
   classifier stays on the LLM until step 4. On production (`compose.production.yml`, unchanged
   here) the same two secret entries (`TYPESAFE_API_KEY` on the gateway and the worker) go in with
   the key file in that deployment's own secret directory.
4. Check the audit rows (below) for a few days of real use, then flip
   `CUSTOMER_DECIDE_MODE_DEFAULT` to `cascade` last, as its own change, and check the `signals`
   rows the same way.

**Audit queries** (tenant-scoped; rows hold typed answers and confidences, never message text):

```sql
-- Staff areas: how often Jev decided, fell back, and why.
SELECT node, mode, provider, fallback_reason, count(*) AS n, round(avg(latency_ms)) AS avg_ms
FROM public.decision_log
WHERE client_id = '<CLIENT_UUID>' AND node LIKE 'assistant_%'
  AND created_at > now() - interval '1 day'
GROUP BY node, mode, provider, fallback_reason ORDER BY node, n DESC;

-- Where Jev and the fallback disagreed (the audit sample, and every injection check).
SELECT created_at, node, disagree, answers, final
FROM public.decision_log
WHERE client_id = '<CLIENT_UUID>' AND node LIKE 'assistant_%' AND cardinality(disagree) > 0
ORDER BY id DESC LIMIT 50;

-- Customer classifier (node 'signals'): same shape.
SELECT mode, provider, fallback_reason, count(*) AS n
FROM public.decision_log
WHERE client_id = '<CLIENT_UUID>' AND node = 'signals'
  AND created_at > now() - interval '1 day'
GROUP BY mode, provider, fallback_reason ORDER BY n DESC;
```

A rising `jev_timeout` or `jev_http_*` count, or many `low_confidence` fallbacks, means Jev is not
helping that area: raise its floor or set it to `llm`.

**The secret file.** The key is never in the repository, the image or the environment file. Staging
mounts it as a Compose secret named by `CSA_STAGING_JEV_SECRET` (default
`staging_disabled_jev_key`, the same placeholder file as the disabled model key). Rotate it by
writing the new value to the same file and recreating the gateway and worker.

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
