# Runbook (dev stack)

Never point any of this at production databases, credentials, bots, services, or hostnames.
The development environment may share a physical host with production; verify the private dev
configuration and use only the isolated dev stack.

For the latest guarded console runtime and recovery constraints, start with
[PROGRESS.md](./PROGRESS.md) and the [release contract](../deploy/RELEASE.md). The generic
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
The original publication recorded
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
or alongside builds. Measured steady-state results, cold-pool failures and queue throughput from
2026-10-12 are summarized in [ARCHITECTURE.md](./ARCHITECTURE.md#evaluation-and-regression-evidence).

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

See [ARCHITECTURE.md](./ARCHITECTURE.md#evaluation-and-regression-evidence) for the measured comparison. Review Jev
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
When the customer's previous inbound message is more than 24 hours old (`LADDER_STALE_AFTER` in
`plan_rules.py`, read in `get_conversation_state.sql` as `ladder_stale`), the stored count is loaded as
0, so a customer who comes back after a day starts at the first clarifying question. Staff replies
and reminders do not count as the customer's activity.
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

## Customer onboarding: sign-ups and linked contacts

PR 2b (migrations 0580 and 0581). Customers sign up for a plan in the chat, and the same person on
WhatsApp and Telegram becomes one customer. The staff side is the Subscriptions page (its Sign-ups tab,
the Customers tab and a subscription's "Linked contacts" panel); the bot side only exists for a company
whose `subscription_agent` capability is on (a company that only sells through TikTok Shop gets no
sign-up wording at all). The owner's checks are in [CUSTOMER_BOT_RETEST.md](CUSTOMER_BOT_RETEST.md),
section "Customer onboarding".

**Read the Sign-ups list.** The tab badge is the number of open sign-ups ("50+" when a further page
exists; no badge while the list is empty or could not be read). Nothing pushes a notification to staff
for a new sign-up in this release, so someone has to open the tab. The tab has five parts, top to
bottom: the expiry setting, "Open sign-ups", "Needs review", "Recent links" and "Decided sign-ups". An
open row shows:

- the name and phone number the customer typed, and the plan they asked for;
- a mark beside the phone: "Verified" means the channel proved the number (the WhatsApp number the
  customer writes from, or Telegram's share-my-number button); "Not verified" means the customer typed
  it. A typed number is kept on the sign-up request only. It is never copied to the customer's contact
  record and it never links two contacts, so check it before you rely on it (call or message the number
  through the usual channel);
- a "Customer record" line when the contact's own name differs from the typed name;
- when the sign-up was made and on which channel.

**Approve** makes one subscription with the status "Waiting to start" (pending) on that plan and marks
the sign-up approved; the customer is not messaged and nothing is charged. **Decline** takes an optional
reason that only staff see; it is never sent to the customer. If a colleague decided the same sign-up
first, the answer is `signup_not_pending` and the list reloads. `signup_has_plan` means the customer
already has a live subscription on that plan (added by hand, or approved from another sign-up): decline
the extra sign-up. `plan_not_open` means the plan was archived or closed to sign-ups after the customer
asked. "Decided sign-ups" shows each result as "Approved by staff", "Approved automatically",
"Declined" or "Expired". A plan whose Sign-ups setting is "Sign up automatically" skips the open list:
the sign-up is approved straight away and the subscription waits to start. "Closed to new sign-ups" on a
plan stops the bot offering it; people already on it keep it. Archiving a plan is not blocked by open
sign-ups; closing it to sign-ups is how a company stops them.

**Tell the customer.** Approve, decline and expiry do not message the customer, so after approving or
declining a sign-up, message the customer from the Inbox yourself (an automatic notice is a follow-up). If the customer's chat was moved by a link, the Inbox answers 409 "This conversation has no active channel to reply on." until the customer writes again on that channel (known follow-up); reply on the channel the customer last used, or wait for their next message.

**Start or cancel a pending subscription.** Until payments exist (a later project) nothing creates
invoices for these subscriptions, so staff start them. Open Customers, choose the "Waiting to start"
filter and open the subscription. "Start subscription" starts it now, sets its first renewal one billing
period from today and charges nothing. "Cancel now" ends it at once; a cancelled subscription cannot be
started again, so add a new subscription if the customer comes back. A pending subscription cannot be
paused, resumed, changed or extended. A plan with a waiting subscription cannot change its billing
interval; archiving it is not blocked.

**The expiry setting.** Sign-ups tab, "Pending sign-ups expire after N days (0 = never)", one whole number
from 0 to 90 for the company, default 14 when nothing was saved. The subscriptions-due job (every five
minutes) cancels every pending subscription that was approved more than N days ago (rule five of that job, after
the four renewal rules; it skips a company with no active Subscriptions package) and marks the sign-up
it came from "Expired". Each cancelled subscription gets a `pending_expired` event on its timeline
(actor `system:subscriptions_due`); nobody is messaged. Changing the setting applies to every pending
subscription from the next run, including old ones. If two people save at the same moment the second gets
a version conflict; the console reads the setting again and the person saves once more. To see the effect:

```sql
SELECT subscription_id, created_at FROM public.subscription_events
WHERE client_id = '<client id>' AND action = 'pending_expired' ORDER BY created_at DESC LIMIT 20;
```

**Link and unlink.** One customer who writes on WhatsApp and on Telegram is two contacts until they are
linked. The system links two contacts by itself in one case: a phone number a channel proved belongs to
exactly one other live contact of the company (this happens when a customer writes on WhatsApp, and when
a customer uses Telegram's share-my-number button), whether that other contact's number was verified or
already on the contact. A number a customer only typed in the chat never links anything, and no console
page edits a contact's phone: when two contacts are the same person, staff link them by hand from a
subscription's "Linked contacts" panel ("Link another contact", then "Link contacts") or from a review
("Link them").

- "Recent links" on the Sign-ups tab lists the company's 20 newest links, newest first, whoever the
  contacts are (also a link between two contacts with no subscription, which no drawer shows): both
  contacts' names and channels, how they were linked ("Matched by a verified phone number" or "Linked by
  staff"), the date, and Unlink while the link can still be undone. Read-only members see the list without
  Unlink.

- The survivor is the contact that already holds a subscription, otherwise the older one. The other
  contact (the loser) stays in the database, hidden, and everything it owned moves to the survivor:
  conversations, orders, tickets, subscriptions, bookings, invoices, sign-up requests and the rest.
  Nothing is copied and nothing is deleted. Messages from either channel then land on the one customer.
- Unlink is on the same panel (and in "Recent links") and works for 30 days after the link, newest link
  first: while a newer
  link on either contact is active the older one cannot be undone (`later_link_active`), and after 30
  days it cannot be undone at all (`unlink_window_closed`; the only fix is to link the other way). A link
  already undone is `link_not_active`, and `survivor_merged` means the survivor was linked into another
  contact since: undo that link first. Unlink moves back only the rows the link moved; rows created after
  the link (new messages, a new order) stay with the survivor.
- "Skipped" is what unlink could not put back. The contact's phone number, phone verification and
  Telegram account go back only while they still hold what the link left; a value staff changed since, or
  one another contact now holds, is left alone instead of being overwritten or failing. The console says
  "Unlinked. Not everything could be put back ..." and names each one; the API returns the same list as
  `skipped` entries of the form `survivor.telegram_id` or `loser.phone` and the audit row
  `contacts.link.removed` records it. Check those values on the two contacts afterwards.
- An unlinked pair is never linked automatically again. If the same phone turns up for the pair, a review
  is opened (reason `previously_unlinked`) and nothing else changes. Staff can still link that pair
  themselves.
- Audit rows: `contacts.link.created`, `contacts.link.removed`, `contacts.review.linked` and
  `contacts.review.dismissed`.

**What a review means.** The Sign-ups tab, "Needs review", lists pairs of contacts the system would not
link by itself, with one plain sentence. At most one review is open per pair. "Not the same person" closes
it and changes nothing, and the same pair is not reviewed again for the same reason (a different reason
opens a new review); "Link them" links the pair as staff and is refused with the same reasons (409
`link_refused:<reason>`) where a link is impossible. A review whose contact has since been merged
elsewhere answers `contact_merged`: close it.

| Reason | What happened | What staff should do |
|---|---|---|
| `several_matches` | More than one other live contact has the same phone number. | Look at the contacts (the sign-up list and Customers show names and numbers) and link the two that are the same person, from the panel or "Link them". Close the review if none are. The review names one pair, so check the other contacts with that number too. |
| `telegram_conflict` | Both contacts already have their own Telegram account, so one person would own two. Neither the system nor staff can link them. | Close the review ("Not the same person"). If they are the same person, keep using the contact that holds the subscription and tell the customer to use one Telegram account; the other chat stays a separate customer. |
| `same_channel_identity` | Both contacts already chat on the same channel with different accounts (two WhatsApp numbers, say). Staff cannot link them either. | Same as above: close the review and treat them as two customers, or ask the customer to use one account. |
| `table_conflict` | Both contacts have a record that may exist only once per customer: a waiting (open) sign-up for the same plan, or a staged customer request with the same key. The link is refused and nothing moved. Two subscriptions on the same plan do not clash. | Settle the clash first, usually by declining one of the two open sign-ups for that plan, then link the pair again from the panel. |
| `previously_unlinked` | Staff unlinked this pair before and the same phone has appeared again. | Decide again: "Link them" if they really are one person (it was a mistake to unlink), otherwise "Not the same person". The system will not link them by itself. |

Open reviews and links for one company:

```sql
SELECT reason, status, created_at FROM public.contact_link_reviews
WHERE client_id = '<client id>' AND status = 'open' ORDER BY created_at DESC;
SELECT survivor_id, loser_id, reason, created_at, unlinked_at FROM public.contact_links
WHERE client_id = '<client id>' ORDER BY created_at DESC LIMIT 20;
```

**Links for a company without the Subscriptions package.** Linking runs for every company (WhatsApp and
Telegram shares link contacts whatever the packages), but the link pages and routes are part of
Subscriptions. A company without an active Subscriptions package has no console path to its links, and the
routes answer 403 `package_inactive` for it, a platform admin included. Read its links with the SQL above.
To undo one, the operator either switches the Subscriptions package on for the company, unlinks from
"Recent links" and switches it off again (while it is on, the bot also offers that company's plans and
sign-ups), or asks a developer to run `csa.contacts.linking.unlink_contacts` for the link on the server.
With the package on, the routes are (add `?client_id=<client id>` as a platform admin):

```
GET  /console/v1/subscriptions/links?limit=20              every link, newest first
GET  /console/v1/subscriptions/links?contact_id=<contact>  the links of one contact
POST /console/v1/subscriptions/links/<link id>/unlink      undo one (writers, within 30 days)
```

Whether automatic linking should run only for companies with the Subscriptions package is an open owner
decision.

**Roll back migrations 0580 and 0581.** Needed when going back to an image older than this release: its
readiness check wants the highest applied version to equal its own. Roll back in reverse order: 0581
first, then 0580.

**Roll back migration 0581.** First, with the services still running, unlink every live link: from
"Recent links" on the Sign-ups tab (its 20 newest), or with the routes above (`GET
.../links?limit=200`, then `POST .../links/<link id>/unlink` for each one with `can_unlink`, newest
first). A link cannot be undone once its
table is dropped, rows a link moved stay with the survivor, and a loser whose link is older than 30 days
(so it could not be undone) comes back as an ordinary, empty contact when `merged_into` is dropped. Then
stop the services and, as the database owner and in this order: `REVOKE UPDATE
(contact_id) ON public.agent_preparation_requests FROM csa_app;`, recreate
`trg_preparation_contact_tenant` as `BEFORE INSERT` only (drop it, then `CREATE TRIGGER
trg_preparation_contact_tenant BEFORE INSERT ON public.agent_preparation_requests FOR EACH ROW
EXECUTE FUNCTION public.contact_identity_tenant_check();`), `DROP TABLE
public.contact_link_reviews;`, `DROP TABLE public.contact_links;`, `DROP FUNCTION
public.contacts_belong_to_client();`, `DROP INDEX public.contacts_verified_phone_uq;`, `DROP INDEX
public.contacts_client_phone_digits;`, `ALTER TABLE public.contacts DROP COLUMN merged_into, DROP
COLUMN phone_verified_at, DROP COLUMN phone_digits;` (this rewrites `contacts` again; the link audit
and every verified-phone stamp are lost), `DROP FUNCTION public.my_phone_digits(text);`, and last
`DELETE FROM public.schema_migrations WHERE version = '0581';`, or the runner thinks it is still
applied. Then start the older images.

**Roll back migration 0580.** After 0581 is rolled back. Stop the services, then, as the
database owner and in this order: `UPDATE public.subscriptions SET status = 'cancelled', cancelled_at =
now() WHERE status = 'pending';` (not a `DELETE`: every pending subscription has a `signup_approved`
event and `subscription_events_subscription_id_fkey` does not cascade, so a delete fails),
`ALTER TABLE public.subscriptions DROP CONSTRAINT subscriptions_status_chk;` then `ALTER TABLE public.subscriptions ADD CONSTRAINT subscriptions_status_chk
CHECK (status IN ('active', 'paused', 'cancelled'));`, `ALTER TABLE public.subscriptions DROP COLUMN
activated_at;`, `ALTER TABLE public.plans DROP COLUMN is_signup_open, DROP COLUMN signup_mode;`, `DROP
TABLE public.subscription_settings;`, `DROP TABLE public.signup_requests;` (every sign-up request is
lost), and last `DELETE FROM public.schema_migrations WHERE version = '0580';`, or the runner thinks it
is still applied. Then start the older images.

**Roll back migrations 0600 and 0601.** Needed when going back to an image older than the training
video release: its readiness check wants the highest applied version to equal its own. Roll back in
reverse order: 0601 first, then 0600.

**Roll back migration 0601.** Stop the services, then, as the database owner: `DROP TABLE
public.training_video_usage;` (the rolling 24-hour processing count is lost), `DROP TABLE
public.training_transcript_windows, public.training_transcript_segments,
public.training_video_jobs, public.training_video_pieces, public.training_video_uploads;` (every
transcript and any upload in progress is lost; stored video files stay in the training store with no
row and the store's sweep removes them), and last `DELETE FROM public.schema_migrations WHERE version
= '0601';`, or the runner thinks it is still applied.

**Roll back migration 0600.** After 0601 is rolled back. Stop the services, then, as the database
owner and in this order: `DELETE FROM public.training_items WHERE kind = 'video';` (restore `NOT NULL`
only when no video row exists, because a video has no `view_mode`, `page_count` or, while uploading,
`sha256`), `ALTER TABLE public.training_items` dropping the constraints `training_items_sha256_chk`,
`training_items_pdf_ready_chk`, `training_items_shape_chk`, `training_items_audio_codec_chk`,
`training_items_video_codec_chk`, `training_items_failure_code_chk`,
`training_items_transcript_language_chk`, `training_items_speech_chk`, `training_items_container_chk`,
`training_items_duration_chk`, `training_items_status_chk`, `training_items_kind_chk` and
`training_items_client_id_id_key`, then dropping the columns `audio_codec`, `video_codec`,
`failure_code`, `transcript_language`, `speech`, `container`, `duration_ms` and `status`, then `ALTER
COLUMN view_mode SET NOT NULL, ALTER COLUMN page_count SET NOT NULL, ALTER COLUMN sha256 SET NOT NULL;`
and `ADD CONSTRAINT training_items_kind_check CHECK (kind IN ('pdf'));`, and last `DELETE FROM
public.schema_migrations WHERE version = '0600';`, or the runner thinks it is still applied. Then start
the older images.

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

## Training video: the training worker

Video in the Training library is processed by one more service, the training worker
(`python -m apps.training_worker.main`, the container `csa-staging-training-worker-1` on staging and
`csa-prod-training-worker-1` on production). What it does and how a job moves is in
[TRAINING_LIBRARY.md](./TRAINING_LIBRARY.md#video); the owner's steps and rollback notes for the
rollout are in [deploy/PRODUCTION.md](../deploy/PRODUCTION.md#training-video). Everything here is
read-only unless it says it changes something. Where a statement says "as the database owner", use
the owner role on the staging database container: `docker exec -i csa-staging-staging-db-1 psql -U csa
-d csa_staging` (production: `csa-prod-staging-db-1`). The owner role is not under row security
(`ENABLE`, never `FORCE`), so it sees every company: it is for looking, not for editing by hand.

**Turn the feature on.** `TRAINING_VIDEO_ENABLED` is off by default, and the worker idles (it opens
nothing and only touches its liveness file) until it is on, so the service can ship first. The order
of the owner's steps, from `deploy/PRODUCTION.md`:

1. Production host capacity: `docker stats --no-stream`, `free -m` and `df -h /` from csa-prod at a
   busy time, taken before the first production deploy of this change and again before the switch.
2. The training worker's S3 key (`csa-prod-training`, policy in
   [OBJECT_STORAGE.md](./OBJECT_STORAGE.md#iam-policy-for-the-training-worker-key)) and its two secret
   files.
3. The application key gains `s3:AbortMultipartUpload` and `s3:ListMultipartUploadParts` on
   `training/*`.
4. The bucket's rule for incomplete multipart uploads goes from 1 day to 2.
5. The two real-bucket checks in [OBJECT_STORAGE.md](./OBJECT_STORAGE.md) ("Checks only the real
   bucket can answer").
6. Review the speech provider's retention terms for training recordings.
7. Staging only: check free disk on the staging host (below).
8. The switch itself: a reviewed pull request that sets `CSA_STAGING_TRAINING_VIDEO=true` in
   `deploy/staging-rollout.env`, and for production also in `deploy/production-rollout.env` with
   `CSA_PROD_S3_TRAINING_KEY=prod_s3_training`. Production's plan refuses video on without the key.
   Staging goes first; production only after steps 1 to 6 and the staging retest.
9. After the switch: `docker exec csa-prod-gateway-1 python -m scripts.storage_smoke` (every line
   PASS), then [TRAINING_RETEST.md](./TRAINING_RETEST.md).

**Read the logs.** `docker logs csa-staging-training-worker-1` (or the production name). Lines are
structured events: the event name, then `key=value` pairs. They carry ids (`client_id`, `item_id`),
`stage`, `attempt`, chunk indexes, byte counts, durations and outcome codes, and never a title, a file
name, a URL, transcript text or ffmpeg's own output. The events to know:

| Event | Means |
|---|---|
| `training_worker_idle` (`outcome=video_off`) | Video is off: the worker is waiting, doing nothing |
| `training_worker_started` | Video is on and both loops are running |
| `training_video_job` | One attempt ended: `outcome` is `ready`, `summarising` (moved on to the summary), `queued` (will retry), `waiting` (daily cap), `failed` or `lost` (the item was removed or the job claimed again); `code` is the failure code; `duration_ms` |
| `training_video_job_stale` (warning) | A job with no heartbeat for 10 minutes was requeued (`outcome=queued`) or failed (`outcome=failed`, attempts used up) |
| `training_video_stage_error` (error) | An unexpected error inside a stage; the job is retried, then fails with the stage's retryable code. `outcome` is the exception class |
| `training_video_job_error` (error) | A database error while claiming or running one company's job; the others go on |
| `training_transcribe_probe`, `_chunk`, `_windows`, `_decided`, `_failed` | The steps of transcribing: the probe, each audio chunk (`chunk`, `chunks_total`, `audio_bytes`), the transcript windows (`segments`, `windows`, `model`), the speech verdict, a failure |
| `training_media_child` | One ffprobe or ffmpeg child finished (duration and output size only) |
| `training_sweep` | The hourly sweep's totals: `abandoned`, `removed`, `aborted`, `pruned` |
| `training_upload_abandoned` | An upload with no piece for 24 hours was removed |
| `training_abort_failed`, `training_file_delete_failed`, `training_sweep_failed`, `training_jobs_loop_error`, `training_sweep_loop_error` | Something failed and will be tried again (an abort or delete by the next sweep, a loop on its next turn) |

A worker that dies is restarted by compose; its healthcheck is the liveness file
(`/tmp/training-worker.alive`, touched every 5 seconds; the compose check allows 90). A loop that stops ends the process rather
than hiding. A worker that refuses to start prints why (a missing speech endpoint, no training store,
the placeholder S3 key) and exits.

**See the jobs by status and age** (as the database owner; read-only):

```sql
-- How many jobs, by stage and status, and how long the oldest has been waiting.
SELECT stage, status, count(*) AS jobs,
       round(extract(epoch FROM now() - min(created_at))) AS oldest_job_age_s
FROM public.training_video_jobs
GROUP BY stage, status
ORDER BY stage, status;

-- Every job that is not done, with the heartbeat age and when a queued job is due.
SELECT j.client_id, j.item_id, j.stage, j.status, j.attempts, j.chunks_done, j.chunks_total,
       j.error_code,
       round(extract(epoch FROM now() - j.created_at)) AS job_age_s,
       round(extract(epoch FROM now() - j.locked_at)) AS heartbeat_age_s,
       round(extract(epoch FROM j.next_attempt_at - now())) AS due_in_s,
       i.status AS item_status, i.failure_code, i.duration_ms / 1000 AS video_s
FROM public.training_video_jobs j
JOIN public.training_items i ON i.client_id = j.client_id AND i.id = j.item_id
WHERE j.status <> 'done'
ORDER BY j.created_at;
```

Reading it: `processing` with a growing `chunks_done` is working. `queued` with a positive `due_in_s` is
waiting out a backoff (60 s times the attempt number) or the daily cap (`error_code` is
`training_processing_limit`). `failed` rows with a retryable code are what managers see "Try again" on.
One worker handles one job at a time, so a long video delays every company's next job.

**A job stuck in `processing`.** The worker refreshes `locked_at` (the heartbeat) after every chunk and
every part. A job whose heartbeat is older than 600 seconds is taken for dead and requeued
automatically: on the worker's next poll (every 5 seconds while idle) it goes back to `queued` and
resumes at `chunks_done` or its saved summary parts, or fails with its retryable code when its three
attempts are used (`training_video_job_stale` in the log). There is nothing to unstick by hand. Check,
in this order: `heartbeat_age_s` above 600 on a `processing` row means no worker has beaten for it, so
look at the container (`docker ps`, `docker logs`) and start it if it is down; a stopped worker's job
costs one of its 3 attempts when it is requeued. A fresh heartbeat with no `chunks_done` movement for a
long time is a slow provider or a very long chunk: the log shows the last `training_transcribe_chunk`.
Do not edit `training_video_jobs` by hand while the worker runs.

**"Try again" and its SQL equivalent.** A manager sees "Try again" on a failed video whose failure is
`training_transcription_unavailable`, `training_summary_unavailable` or `training_storage_unavailable`
(`POST /training/items/{id}/retry`): the job goes back to `queued` at the stage that failed with its
attempts reset, the item shows that stage again, and the worker picks it up (it works even while video
is off; the job then waits for the worker). A refusal of the file itself (`training_video_unreadable`,
`training_video_too_long`, `training_video_codec`) cannot be retried: the file is deleted and a new one
must be added. The same change by hand, as the database owner, in one transaction (it writes no
`training.video_retried` audit row, which the route does; replace `<item id>`):

```sql
BEGIN;
WITH job AS (
    UPDATE public.training_video_jobs j
    SET status = 'queued', attempts = 0, error_code = NULL, locked_at = NULL,
        next_attempt_at = now()
    FROM public.training_items i
    WHERE j.item_id = '<item id>' AND j.status = 'failed'
      AND i.client_id = j.client_id AND i.id = j.item_id
      AND i.kind = 'video' AND i.status = 'failed'
      AND i.failure_code IN ('training_transcription_unavailable',
                             'training_summary_unavailable', 'training_storage_unavailable')
    RETURNING j.client_id, j.item_id, j.stage
)
UPDATE public.training_items i
SET status = job.stage, failure_code = NULL, updated_at = now()
FROM job
WHERE i.client_id = job.client_id AND i.id = job.item_id
RETURNING i.id, i.status;
COMMIT;
```

No row back means the item is not a failed video with a retryable code; roll back and look at it.

**The daily cap.** A company's videos may use at most 5 hours of processing in any rolling 24 hours
(`DAILY_PROCESSING_SECONDS`, a constant in `csa/training/limits.py`, not a setting). A video's
duration is booked, rounded up to the second, before its first chunk is sent; deleting the video does
not give it back, and a retry of an already booked video does not book it again until its usage row
is pruned after 2 days. A video that does not fit waits: its job is `queued` with `error_code`
`training_processing_limit`, `due_in_s` says when enough booked time has aged out, and managers see
"Waiting to start". To see what each company has booked (as the database owner; read-only):

```sql
SELECT client_id, sum(seconds) AS booked_s, 18000 - sum(seconds) AS room_s,
       min(processed_at) + interval '24 hours' AS oldest_booking_ages_out_at
FROM public.training_video_usage
WHERE processed_at > now() - interval '24 hours'
GROUP BY client_id
ORDER BY booked_s DESC;
```

Deleting a company's usage rows would lift its cap, and would also let a delete-and-upload loop run
without limit: do it only as an owner decision, for that one company.

**The hourly sweep.** The worker sweeps every active company when it starts and every hour after:
uploads with no new piece for 24 hours are removed (audit `training.upload_abandoned`) and their store
uploads aborted; stored files no row keeps are deleted; on S3, open multipart uploads with no
`uploading` row are aborted; usage rows older than 2 days are deleted. The totals are in the
`training_sweep` line. Restarting the worker runs one at once. To see what is open (as the database
owner; read-only):

```sql
SELECT u.client_id, u.item_id, u.piece_count, u.declared_bytes,
       (SELECT count(*) FROM public.training_video_pieces p
         WHERE p.client_id = u.client_id AND p.item_id = u.item_id) AS pieces,
       round(extract(epoch FROM now() - coalesce(u.last_piece_at, u.created_at))) AS idle_s
FROM public.training_video_uploads u
ORDER BY idle_s DESC;
```

A `training_abort_failed` line that repeats every hour on S3 means the key that tried lacks
`s3:AbortMultipartUpload` on `training/*`: the gateway's application key (on a removal) or the
training worker's key (the sweep). The bucket's lifecycle rule clears the upload after 2 days
meanwhile.

**Staging disk.** Staging keeps videos on the `staging-training-uploads` volume, up to 5 GiB per
company (uploads in progress count at their declared size), and the worker writes one 5-minute audio
chunk at a time under `/tmp`. The staging host's disk has run full before: check it before switching
video on and after a retest with large files.

```bash
df -h /
IMAGE=$(docker --host unix:///var/run/docker.sock inspect csa-staging-gateway-1 --format '{{.Image}}')
docker --host unix:///var/run/docker.sock run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --user 1000:1000 \
  -v csa-staging_staging-training-uploads:/data:ro \
  --entrypoint du "$IMAGE" -sh /data
```

The image the stack runs is 378 MB bigger than before video (901 MB against 523 MB), and the previous
image stays on the host for a rollback. A staging reset empties the volume with the database
(`scripts.staging_reset`).

**Remove the idle training worker after a downgrade past this change.** A rollback to an image from
before this change leaves the training worker container running, idle, on the newer image: staging's
older runner does not know the service, and production's runner from `main` stops at `recreate
training-worker` after the other services are back on the older images. Remove it by hand:
`docker rm -f csa-staging-training-worker-1` on staging, `docker rm -f csa-prod-training-worker-1` on
production. The migrations roll back as described above ("Roll back migrations 0600 and 0601").

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
