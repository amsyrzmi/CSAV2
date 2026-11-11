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

The floors are per question **type** and global: `ASSISTANT_JEV_FLOOR_NOUL` applies to every yes/no
question in every area (the routing groups, the email decider's `needs:subscription`, `needs:kb`
and `send`, and the injection check), and `ASSISTANT_JEV_FLOOR_CHOICE` to every choice question
(today only the email decider's `mode`). Raising one to fix one area raises it for all of them.

**Staging switches.** Staging services take their environment only from
`deploy/staging-rollout.env` (`scripts/staging_rollout.py` builds the whole environment from it;
nothing else is inherited). These optional lines set the settings above; each is checked when the
file is read, and a value outside its set or range stops `plan` and `apply` before anything runs.
Unset, compose uses the settings' own default, so nothing changes. The file's header lists only
the eight required keys; these optional lines are allowed too. Adding or changing one is a reviewed
PR, then a rollout with the `staging-rollout` skill (the service whose environment changed is
recreated).

| Line in `deploy/staging-rollout.env` | Values | Sets (service) |
| --- | --- | --- |
| `CSA_STAGING_JEV_SECRET` | `staging_disabled_jev_key` (default), `staging_jev_key` | which secret is mounted as `TYPESAFE_API_KEY` (gateway and worker) |
| `CSA_STAGING_ASSISTANT_DECIDER_EMAIL` | `auto` (default), `llm`, `shadow`, `cascade` | `ASSISTANT_DECIDER_EMAIL` (gateway) |
| `CSA_STAGING_ASSISTANT_DECIDER_ROUTING` | same | `ASSISTANT_DECIDER_ROUTING` (gateway) |
| `CSA_STAGING_ASSISTANT_DECIDER_INBOUND_INJECTION` | same | `ASSISTANT_DECIDER_INBOUND_INJECTION` (gateway) |
| `CSA_STAGING_JEV_FLOOR_NOUL` | a decimal from 0.5 to 1.0 (default 0.7) | `ASSISTANT_JEV_FLOOR_NOUL` (gateway) |
| `CSA_STAGING_JEV_FLOOR_CHOICE` | a decimal from 0.5 to 1.0 (default 0.7) | `ASSISTANT_JEV_FLOOR_CHOICE` (gateway) |
| `CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT` | `llm` (default), `cascade` | `CUSTOMER_DECIDE_MODE_DEFAULT` (worker) |

`CSA_STAGING_JEV_SECRET` accepts only those two names: any other declared secret (a database DSN,
the Groq key) would be sent to the external Jev API. A decider at `shadow` or `cascade`, or the
customer default at `cascade`, also needs `CSA_STAGING_JEV_SECRET=staging_jev_key` in the same file,
or the file is refused (the gateway would not start, and the customer default would quietly stay on
the LLM). The audit sample has no staging switch; it stays at 0.10.

**Kill switch.** Per area, on staging: a reviewed PR adds `CSA_STAGING_ASSISTANT_DECIDER_EMAIL=llm`
(or `_ROUTING`, `_INBOUND_INJECTION`) to `deploy/staging-rollout.env`, then roll out; `llm` needs no
key and is always accepted. Everything at once: a reviewed PR removes the
`CSA_STAGING_JEV_SECRET=staging_jev_key` line (and any `shadow`/`cascade` lines, which need it),
then roll out. The customer classifier: a reviewed PR sets
`CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT=llm` (or removes the line), then roll out; or, at once and
for one company, `csa.ops.runbooks.force_decide_mode(conn, client_id, "llm")` (runbook 04), which
is read on the next turn and wins over the default. Elsewhere (development, production) set the
same settings in that deployment's environment and restart the gateway or worker.

**Rollout order** (each step its own reviewed change; the owner does the key steps):

1. Roll out the images and migration 0542 with no key wired into the services. Every area runs on
   the LLM or rule. (A rollout with no key plans and applies cleanly: the staging compose files map
   the disabled placeholder to `TYPESAFE_API_KEY`, and the real key's file is not asked for.)
2. **Owner: run the staff evaluation** from a checkout (no settings or database needed):

   ```sh
   uv run python -m scripts.assistant_jev_eval
   ```

   It asks for the key at a hidden prompt (nothing echoed, nothing in shell history; never put the
   key on the command line or in a repo file). It prints, per area and question, agreement with the
   labelled English and Malay messages (written cases plus the smoke messages) on the answers at or
   above the floor, the coverage, and whether the question meets its target: 90 percent, 95 for
   the injection check, with at least 5 confident answers. It prints counts and case ids, never
   message text. Exit 0 passes, 2 means it could not start, 1 means a question is below target.
   For a 1, either keep that area on `llm` (step 3 then adds its `llm` line with the key), or find
   a floor that passes by running again with a higher floor for that question's type:

   ```sh
   uv run python -m scripts.assistant_jev_eval --floor-noul 0.8 --floor-choice 0.7
   ```

   Run without the flags, it repeats the default 0.7 floors. A higher floor means fewer confident
   answers (lower coverage, more fallbacks) and applies to every question of that type in every
   area, so read the whole report again, not only the question that failed. The floor that passes
   is applied on staging by `CSA_STAGING_JEV_FLOOR_NOUL` / `CSA_STAGING_JEV_FLOOR_CHOICE` in the
   step 3 PR. Put the report (it holds no message text) in that PR.
3. **Owner: write the key file, then wire it.** Only after step 2 passes. On the staging host, as
   your own user (UID 1000, the runtime UID; not with sudo), from the main checkout:

   ```sh
   uv run python -m scripts.staging_jev_key --secret-dir /srv/csa-staging/secrets
   ```

   It checks the secret layout first (as `staging_secrets check` does), asks for the key at a
   hidden prompt, writes `pipeline/staging_jev_key` (mode 0400, no trailing newline), refuses to
   overwrite an existing file or follow a symlink, and prints only the file's path. Then a reviewed
   PR adds `CSA_STAGING_JEV_SECRET=staging_jev_key` to `deploy/staging-rollout.env`, with any floor
   or `llm` lines from step 2, and you roll out with the `staging-rollout` skill (`plan` stops,
   exit 2, until the key file exists). That turns the staff areas on in the gateway, because `auto`
   becomes `cascade`. The worker has the key from then on too. For a company with **no** stored
   `decide_mode` its classifier stays on the LLM until step 4; a company whose policy already
   stores `shadow`, `cascade` or `jev` (forced earlier, or the local dev tenant's `cascade`) goes
   live on Jev at this step. Check for such companies before the step 3 PR (owner connection):
   `SELECT id FROM public.clients WHERE config->'policy' ? 'decide_mode' AND config->'policy'->>'decide_mode' <> 'llm';`.
   On production (`compose.production.yml`, unchanged here) the same two secret entries
   (`TYPESAFE_API_KEY` on the gateway and the worker) go in with the key file in that
   deployment's own secret directory.
4. **Owner: run the customer gate, then flip the customer default last.** After a few days of
   real use with the staff audit rows (below) looking right, run the customer classifier's own
   evaluation on your workstation, from the main checkout, where your ignored `.env.local` holds
   the development DSNs, `GROQ_API_KEY` and `TYPESAFE_API_KEY` (you enter them there yourself;
   the run reads settings, so it needs all three even though it touches no database):

   ```sh
   ENV=dev uv run python -m csa.evals.decisions --provider both --concurrency 1 --out "$HOME/csa-evals/decision-report.md"
   ```

   Keep `--out` outside the repository: without it the report is written to
   `docs/evals/decision_report.md` in the checkout. It sends synthetic case text only. The gate
   is the **`jev` row** of its table: the `intent`, `language` and `urgent` columns at least 90
   percent, and the `wants_human`, `injection`, `injection recall` and `human recall` columns at
   least 95 percent. These numbers count every Jev answer, an error as a miss, whatever its intent
   confidence; the cascade uses Jev only at or above the company's `jev_min_confidence` (default
   0.5) and gives the rest to the LLM, so the gate is stricter than what production uses and holds
   at any `jev_min_confidence`. If every number meets its target, a reviewed PR (with the table)
   adds `CSA_STAGING_CUSTOMER_DECIDE_MODE_DEFAULT=cascade`; roll out, then check the `signals`
   rows as below. If any number is below its target, the customer default stays `llm`; a single
   company can still be put on `cascade` by hand with `force_decide_mode` once its own `signals`
   rows have been checked in `shadow`.

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

Many disagreements on confident answers in an area mean its floor is too low: find a passing floor
with the evaluation (step 2) and set it with the floor switch, or set the area to `llm`. A rising
`jev_timeout` or `jev_http_*` count, or mostly `low_confidence` fallbacks, means Jev is not helping
that area: set it to `llm`.

**The secret file.** The key is never in the repository, the image or the environment file. Staging
mounts it as a Compose secret named by `CSA_STAGING_JEV_SECRET` (default
`staging_disabled_jev_key`, the same placeholder file as the disabled model key);
`staging_secrets check` verifies the real file's owner and mode whenever it exists. To rotate the
key: remove the old file (`rm -f /srv/csa-staging/secrets/pipeline/staging_jev_key`; the file is
mode 0400, so a plain `rm` stops to ask, but the directory is yours), run `scripts.staging_jev_key`
again with the new key, then restart the gateway and worker so they mount and read the new file:
`docker --host unix:///var/run/docker.sock restart csa-staging-gateway-1 csa-staging-worker-1`.
Restarting (not recreating) the two containers should re-bind the new file, since a Compose file
secret is a bind mount by path, but this is untried: after the first rotation, run the first
`decision_log` query above and check that Jev answers again (provider `jev`, no new `jev_http_*`
fallbacks) before relying on it.

## Customer bot conversation memory

The customer bot is shown the earlier messages of the chat it is answering: the last 20 messages,
each cut to 200 characters, with PII already redacted in the stored body (never the raw text). The
classifier, the planner and the reply writer all see them, so the bot can avoid repeating itself
and can follow a short answer such as "yes" or "the second one".

**Kill switch.** `clients.config.policy.conversation_memory` (default `true`) is a per-company
setting. When it is `false`, no earlier message reaches the reply writer (compose), the handoff
reply or the reply check, and no reply carries the already-notified sentence ("a team member has
already been told"); the read of earlier handoffs is skipped too. One exception: for a company
with bookings, the booking planner keeps its own history of the last 6 messages for the classifier
and the planner, as it had before this release, so a short answer such as "3 pm" still continues a
booking. The setting is read on every turn, so a change takes effect on the company's next message
with no restart or rollout. There is no console control for it; switch it from the owner
connection. The nested
`jsonb_set` creates `policy` if the company has none (a single `jsonb_set(config, '{policy,conversation_memory}', ...)`
does nothing when `config->'policy'` is missing):

```sql
-- Off for one company
UPDATE public.clients
SET config = jsonb_set(
        jsonb_set(COALESCE(config, '{}'::jsonb), '{policy}', COALESCE(config->'policy', '{}'::jsonb), true),
        '{policy,conversation_memory}', 'false'::jsonb, true)
WHERE id = '<client id>';

-- On again (explicit)
UPDATE public.clients
SET config = jsonb_set(config, '{policy,conversation_memory}', 'true'::jsonb, true)
WHERE id = '<client id>';

-- Back to the default (remove the key)
UPDATE public.clients SET config = config #- '{policy,conversation_memory}' WHERE id = '<client id>';

-- Read it back (NULL means the key is not set, so the default `true` applies)
SELECT config->'policy'->>'conversation_memory' FROM public.clients WHERE id = '<client id>';
```

Clearing the key or setting it to `true` restores the default. The console's own writes to the
policy (reply mode, approval wait) set single keys with `jsonb_set`, so they leave this key alone.
Write only JSON `true` or `false`: the policy model rejects unknown keys and wrong types, and a
policy that fails validation fails the company's turns. Clear the key (or set it true) before
rolling back to an image older than this release: the older policy model rejects unknown keys and
would fail every turn of that company. The owner's retest rows are in
[CUSTOMER_BOT_RETEST.md](CUSTOMER_BOT_RETEST.md).

### The escalation ladder, the staff note and the gaps

**The ladder.** `conversations.clarify_count` counts the consecutive turns the bot could not answer.
0 and 1: it asks a clarifying question; 2: it gives a partial answer with one targeted question; 3 or
more: it hands off, and the escalation's reason is `unresolved_turns`. `ask_clarifying` and
`partial_answer` add one, chitchat and ignored messages keep the count, any other strategy (an answer,
a confirmation, a handoff) resets it to 0. The count is written once per turn when the turn finishes.
A request for a person, anger, a legal or police word, an injection attempt, a blocked message, a
voice message that could not be read and an unsupported language skip the ladder and hand off at
once, under their own reason (those routes record `plan_escalated`, or `human_requested` and
`angry_customer` when the classifier set them). The ladder works with `conversation_memory` off. There
is no switch for the ladder itself; to see where a chat stands:

```sql
SELECT clarify_count FROM public.conversations WHERE id = '<conversation id>';
```

**The staff note.** Each handoff writes one note on its escalation row, `escalations.note` (at most
500 characters, NULL when none; the assistant writes at most 300). It is for staff only: the inbox
thread shows the newest one as "Note from the assistant (...)", and it is never sent to the customer or
put in the Telegram alert (the alert carries the reason only). A note the model wrote that fails the
grounding check, a model error and every template handoff (a legal word, a blocked message, a voice
failure, an unsupported language) get a fixed English sentence instead. Read the notes of a company:

```sql
SELECT created_at, reason, note
FROM public.escalations
WHERE client_id = '<client id>' AND note IS NOT NULL
ORDER BY created_at DESC LIMIT 20;
```

**Gap topics.** When the handoff follows a question no fact answered, the same model call writes a
short topic (at most six words, no digits) and the turn records it in `kb_gaps` once. The knowledge
agent records its own query for the same question, so one question can leave two rows. Read the open
gaps of a company with `SELECT query, hits, last_seen FROM public.kb_gaps WHERE client_id = '<client id>'
AND NOT resolved ORDER BY last_seen DESC`; the owner's fix is a knowledge-base entry that answers the
topic, not a code change.

**Roll back migration 0560.** Needed when going back to an image older than this release: its readiness
check wants the highest applied version to equal its own, so it reports not ready while 0560 is applied.
Stop the services, then, as the database owner and in this order: `DROP INDEX
public.escalations_conversation_created;`, then `ALTER TABLE public.escalations DROP CONSTRAINT
escalations_note_length;`, then `ALTER TABLE public.escalations DROP COLUMN note;` (every note already
written is lost), and last `DELETE FROM public.schema_migrations WHERE version = '0560';`, or the
runner thinks it is still applied. Then start the older images.

**Replay the new conversations** (after the new image is on staging; synthetic facts, no database):

```bash
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation ms-subscription
docker exec csa-staging-gateway-1 python -m scripts.customer_bot_replay --conversation ms-stock
```

For a handoff the replay also prints the staff note and the gap topic the model would write. The
checklist for the whole release is "Business answers and the escalation ladder" in
[CUSTOMER_BOT_RETEST.md](CUSTOMER_BOT_RETEST.md).

## Stuck knowledge uploads

A failed or half-read upload shows a card under Needs attention on the Business knowledge page. **Try
again** re-queues it from the stored original (no new upload; the tries counter and the error are
reset) and needs the knowledge assistant switched on; it is offered only while the original is still
stored. **Remove** takes the message off the list (the job gets `dismissed_at`) and deletes the job's own
uploaded file (its `kb_upload_files` row is soft-deleted and the bytes are swept after the commit); it
never changes what the assistant knows, and **Clear all that failed** and **Cancel** do the same for the
jobs they act on. The shared-file rule in one sentence: a job's stored upload is deleted only if no
document uses it and no other job still on the list names it, so a file behind a document goes only with
that document. Files left by removals made before this change (the PR #96 behaviour, on staging since
`3d25165`) stay under "Other uploaded files" and have to be deleted by hand there (**Delete source**);
there is no backfill.
Remove, Cancel and Clear all answer `storage_cleanup: pending` when the sweep failed; **Clean up leftover
files** on the Maintenance page finishes it. **Cancel** is for a job still `queued` (for example while the assistant is
switched off); a job a worker is reading cannot be cancelled and finishes, fails or is requeued by the
reaper. Deleting a source or a document closes every job that only that file kept alive: the job ends
`cancelled` and dismissed, so no card is left behind, and a worker that finds its source deleted stores
no document. Delete works with the assistant switched off; when the answer says the storage sweep is
`pending`, **Clean up leftover files** on the Maintenance page finishes it. Migration 0550 cleaned up
the cards already stuck before this release (failed, partial, queued or processing jobs whose
files were all deleted became `cancelled` and dismissed). To check a company by hand, list its jobs
with `dismissed_at IS NULL` in `kb_ingest_jobs`; every audit row for these actions is named
`kb.job_dismissed`, `kb.job_retried`, `kb.job_cancelled` or `kb.jobs_dismissed`.

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
