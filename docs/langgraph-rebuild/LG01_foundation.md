# LG01 — Foundation: repo, DB layer, RLS, tracing, compose, CI

## CONTEXT FOR CODING AGENT
You are rebuilding the CSA platform runtime in Python. Read `LG00_master_plan.md` §3 (principles) and §6 (decisions) first. This phase creates the skeleton only: no business logic. Existing assets you must reuse, not recreate: Postgres `csa_db` (pgvector + PostGIS, 45 tables), numbered SQL migrations in `db/migrations/`, TEI embeddings (`csa_embeddings:8081`), FastMCP server (`csa_mcp:8001`), docker network `csa-net`. Host is Ubuntu "nexusserver"; ports bind to `127.0.0.1`; public access is via Cloudflare Tunnel + Caddy.

## Goal
A running, observable, tenant-safe skeleton: `gateway`, `worker`, `delivery-worker` containers start, talk to Postgres through a single pool, enforce RLS, and emit traces — with CI green.

## Tasks

### 1. Schema alignment (partly done)
You already sent `\d+` for nine tables. Decisions (details in LG02/LG03/LG07):

| Table | Verdict | Notes |
|---|---|---|
| `ingress_events` | **Reuse as the inbound queue** | `status received → dispatched → completed / failed / dead`, `attempts`, `next_attempt_at`, `dispatched_at` (in-flight reaper), `raw` + `raw_purged_at`, `payload_hash`, unique `(client_id, idempotency_key)` and unique `trace_id`. `endpoint_id` is NOT NULL ⇒ every inbound source must be a `webhook_endpoints` row. No `conversation_id` ⇒ lock on a hash of `(client_id, channel, channel_thread)` |
| `outbound_deliveries` | **Reuse as the outbox, add columns** | points at `messages.id` (NOT NULL, unique), `endpoint_id` NOT NULL, status `pending / delivering / delivered / skipped / dead`, `locked_at`, `attempts`. Missing: rendered payload, kind, channel/contact, idempotency key, provider message id (LG07 migration) |
| `outbound_delivery_attempts` | Reuse | `attempt_no, status_code, error, duration_ms` |
| `pending_actions` | **Reuse as the button/confirmation store** | uuid id, bound to conversation + contact, `agent, action, payload, status, expires_at`; **used by none of your seven workflows today** |
| `dlq` | Reuse | `stage, envelope, error`, replay counters (`replay_attempts, max_replays, replayed, abandoned`) |
| `agent_runs` | Reuse | exactly what `Build Log` writes |
| `approvals` | Reuse | `trace_id` doubles as the LangGraph thread id; has `draft, reason, risk_score numeric(3,2), decided_by/at, final_text, expires_at`. Add `UNIQUE(trace_id)` |
| `channel_credentials` | Reuse + encrypt | `shop_id` = external account id (bot id / WA `phone_number_id` / mailbox); tokens are plain `text` today; `meta jsonb` for webhook secret / Gmail `historyId` |
| `capability_registry` | Reuse + 3 columns | already has `timeout_ms, max_retries, requires_human, risk_class, circuit_state, fail_count, opened_at, read/write_scopes, authorization_mode`. **Do not re-add timeout/retries** |
| `webhook_endpoints` | **Reuse as the source registry** | `public_id` (16–64 chars, unique, used in the URL), `credential_id` **UNIQUE** (1 endpoint : 1 credential), `reply_mode sync\|callback`, `sync_wait_ms` 1–60 s (default 20 s), `callback_url` (https only), `rate_limit_rpm` (1–6000, default 120), `is_enabled`, `disabled_reason`. No `channel` column — the channel comes from the credential |
| `messages` | Reuse | `body` NOT NULL (raw), `redacted_body`, `attachments`, `has_attachment`; **`idempotency_key` is globally unique** (prefix keys with the endpoint public id); trigger `trg_webhook_delivery` enqueues a delivery on every outbound insert (function unseen) |
| `conversations` | Reuse | `status, summary, turn_count, clarify_count, sentiment, last_intent, ai_muted_until, owned_by`; unique `(client_id, channel, channel_thread)`; trigger `trg_keep_escalated` (BEFORE UPDATE OF status). Egress only writes `turn_count` today |
| `contacts` | Reuse | identity columns `email, telegram_id, phone, console_user_id` + `tier, locale, address, contact_kind`; unique per client on telegram_id / email / console_user_id |
| `contact_identities` | **Adopt for every channel** | PK `(client_id, channel, external_id)` → `contact_id`, tenant-check trigger; no workflow uses it today |
| `ingress_dedup` | Redundant after cutover | superseded by the unique key on `ingress_events` |
| `approval_audit` | Reuse | `(client_id, approval_id, action, actor, old_values, new_values)` |
| `action_log` | Reuse as the side-effect ledger | unique `(client_id, action_type, idempotency_key)`; `conversation_id` is NOT NULL, so system events with no conversation can't use it |
| `clients` | Reuse | `config jsonb` (policy, budget…), `brand_name`, `brand_tone`, `timezone`, `region`, `languages[]`, `core_object`, `deployment_mode` (default `shadow`), `tokens_per_day` (400000), `status` |
| `voice_transcripts` | Reuse | `status` (no CHECK), `livekit_job_id` (partial unique), `envelope jsonb`, `message_id`, `callback_attempts`, `confidence`, `duration_secs`; `message_id` and `callback_attempts` are never written today; no index for "stuck in processing" |
| `contact_verifications` | Reuse (OTP) | `code_hash`, `pending_field`, `pending_value`, `attempts`/`max_attempts` (3), `status`, `expires_at`, `verified_at` — the identity-linking mechanism |
| `orders` / `order_items` / `order_confirmations` / `order_events` | Reuse + fixes | `orders.id` is **text** `ORD-xxxxxxxx` (global PK, 32-bit random part); status CHECK `draft … refunded`; `orders_channel_chk` excludes WhatsApp/email; `orders.items` is maintained by trigger `trg_sync_order_items`; `order_confirmations` has `id` and a unique pending-per-order index; `order_events.payload jsonb` |
| `products` | Reuse | `stock_qty`, `reserved_qty` (CHECK ≥ 0), generated `available_qty`; duplicate trigram/category indexes |
| `tickets` / `ticket_events` | Reuse + CHECKs | no CHECK on status/priority/category; `idx_tickets_sla` only covers `status='open'`; `conversation_id`/`contact_id` nullable |

Verified from your `db_output.txt`: extensions are `pg_trgm, plpgsql, postgis 3.4.3, uuid-ossp, vector 0.8.6` (**no `pg_cron`, no `pgcrypto`**); exactly six user triggers (`trg_keep_escalated`, `trg_webhook_delivery`, `tickets_set_updated_at`, `trg_sync_order_items`, `trg_webhook_endpoints_updated`, `trg_contact_identity_tenant`), none on `capability_registry` or `agent_runs`; user functions besides the trigger functions: **`csa_expire_drafts`, `csa_expire_pending_approvals`, `csa_retention`, `purge_ingress_raw`** (bodies not yet seen — LG10 §0).
One command replaces all remaining `\d+`/`\sf` requests (schema only, **no data**; it contains every table, constraint, index, trigger and function body, and excludes extension objects):
```
docker exec csa_db pg_dump -U csa -d csa --schema-only --no-owner --no-privileges > csa_schema.sql
```
Commit a reviewed copy as `db/schema_baseline.sql`; it also seeds the dev database (LG12).
Keep every existing trigger/function (`enqueue_webhook_delivery`, `keep_escalated_status`, `contact_identity_tenant_check`, `trg_sync_order_items`, `*_set_updated_at`): they are part of the contract. All new DDL is collected, ordered and made safe for a live n8n in **LG11**.

RLS target list:
```sql
SELECT table_name FROM information_schema.columns
WHERE table_schema='public' AND column_name='client_id' ORDER BY 1;
```
**circuit_state diagnosis** (nothing in your seven workflows writes it):
```sql
SELECT name, circuit_state, fail_count, opened_at FROM capability_registry ORDER BY name;
SELECT tgname, tgrelid::regclass AS tbl, pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal;
SELECT n.nspname, p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname='public' AND p.prokind='f' ORDER BY 2;
```
If no trigger/function touches those columns, the breaker has never worked; LG02 §5 builds it.

### 2. Scaffold
- `uv init`; Python 3.12; `pyproject.toml` with exact pins (not ranges) for: `fastapi`, `uvicorn[standard]`, `psycopg[binary,pool]` (v3), `langgraph`, `langgraph-checkpoint-postgres`, `pydantic>=2`, `pydantic-settings`, `httpx`, `tenacity`, `structlog`, `orjson`, `jsonschema`, `typesafe-sdk`, `langchain-core` + `langchain-groq` (LLM provider only), `opentelemetry-*`, dev: `pytest`, `pytest-asyncio`, `ruff`, `pyright`, `testcontainers`.
- Pin LangGraph to the current 1.x release you install; record the version in `docs/versions.md`.
- Layout:
```
apps/gateway/main.py        apps/worker/main.py        apps/delivery/main.py
csa/core/{settings,db,tenancy,logging,tracing,ids,clock,pii}.py
csa/contracts/              (LG02)
csa/channels/               (LG03)
csa/graph/                  (LG04, the ONLY package importing langgraph)
csa/decide/                 (LG06)
csa/agents/                 (LG05)
csa/delivery/               (LG07)
csa/evals/                  (LG08)
db/migrations/              (keep your numbered SQL)
db/sql/                     (query files, one per query)
tests/  deploy/
```
- Enforce the rule "only `csa/graph/` imports `langgraph`" with a ruff `banned-api` rule or an import-linter test.

### 3. Settings
`pydantic-settings` class `Settings` reading env + Docker secrets. **No secret literal anywhere in the repo.** Required: `DATABASE_URL`, `LG_DATABASE_URL` (same DB, schema `lg`), `GROQ_API_KEY`, `TYPESAFE_API_KEY` (optional until LG06), `PUBLIC_BASE_URL`, `ENV`. Channel tokens live in `channel_credentials` (encrypted at rest — see Task 6), not env.

### 4. DB layer (`csa/core/db.py`)
- One `psycopg_pool.AsyncConnectionPool` per process; `row_factory=dict_row`.
- Query loader: `await q.fetch_one("orders/status.sql", params)` reading from `db/sql/`. Move your existing SQL (order status, create draft CTE, cancel, expiry sweep, etc.) there verbatim in LG05.
- `tenancy.tx(client_id)` async context manager: opens a transaction and runs `SELECT set_config('app.client_id', %s, true)` (transaction-local).
- Row models: `psycopg.rows.class_row(PydanticModel)`.

### 5. Row-Level Security (defense in depth) — **staged so n8n keeps working**
The `psql` session you pasted connects as `csa`, the **table owner**, and n8n's Postgres credential almost certainly does too *(verify)*. With `FORCE ROW LEVEL SECURITY` the owner is subject to policies, so n8n (which never sets `app.client_id`) would instantly read zero rows. So:
- **Now (coexistence):** `ENABLE` RLS + policies, **no `FORCE`**. The owner `csa` (n8n, migrations, your `SECURITY DEFINER` trigger functions) bypasses; the new stack connects as a separate non-owner role `csa_app`, which is subject to the policies.
- **After the n8n core is retired (LG09):** move n8n/legacy off the owner role, then `FORCE` RLS (LG11 M14).
- Roles: `csa` (owner, migrations), `csa_app` (runtime, RLS enforced), `csa_ro` (analytics/read tools, RLS enforced).
- Policy per tenant table (generated from the RLS target list; DDL in LG11 M01):
```sql
CREATE POLICY tenant_isolation ON <t>
  USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
  WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
```
- Gateway lookups that must run **before** the tenant is known use one narrow `SECURITY DEFINER` function `resolve_endpoint(public_id)` (LG11 M01), not a bypass role.
- Tests: with `app.client_id` = A, `SELECT count(*) FROM orders WHERE client_id = <B>` returns 0 as `csa_app`; with it unset, 0 rows; as `csa` (owner) the n8n queries still work.

### 6. Secrets in the DB
`channel_credentials` holds bot tokens / WA tokens / Gmail refresh tokens in plain `text` columns today. **`pgcrypto` is not installed**, and DB-side encryption keeps the key next to the data anyway. Use **application-level envelope encryption** (AES-GCM or Fernet; one key per environment from Docker secrets/env, never in the DB or git), store ciphertext in `access_token` / `refresh_token` (prefix with a version tag such as `v1:` so you can rotate keys), decrypt only inside the channel adapter at send time, never log credentials. Migration: a one-off script reads each row, encrypts, writes back; n8n workflows that read these columns directly must be moved first or kept on a read path that decrypts (check which workflows read `channel_credentials`). Gateway secrets (webhook signing secrets, app secrets) go in `meta` encrypted the same way.

### 7. LangGraph checkpoint schema
- Schema `lg` in the same Postgres: `CREATE SCHEMA lg;` and set the saver's connection `search_path` to `lg`.
- Run `AsyncPostgresSaver.setup()` once (as `csa_owner`) in a migration script, not at app start.
- If you pass your own pool/connections to the saver, they must be created with `autocommit=True` and `row_factory=dict_row` (documented requirement); `AsyncPostgresSaver.from_conn_string(...)` handles it for you.
- Retention: cron in the worker deletes checkpoints for threads older than N days (start with 14) **unless** the thread is an open approval. Without this the tables grow forever.

### 8. Observability
- `structlog` JSON logs; every log line carries `trace_id`, `client_id`, `conversation_id`, `channel`.
- Start light (you're at ~100 messages/day): structured logs plus the Postgres tables you already have (`agent_runs`, `outbound_delivery_attempts`, `dlq`, new `decision_log`) answer most debugging questions. Emit OpenTelemetry spans from day one (one per graph node; attributes: model, tokens, latency, decision provider + version) but only wire a backend when you need one: LangSmith or Langfuse cloud (redacted data only), or a single-container Jaeger/Tempo. Self-hosting current Langfuse pulls in ClickHouse, Redis and object storage — heavy for one homelab box at this volume *(verify against current docs)*.
- `/healthz` (process up) and `/readyz` (DB reachable, migrations at expected version).
- Prometheus `/metrics` (optional now; Uptime Kuma already watches `/healthz`).

### 9. Compose (`deploy/compose.yml`)
Services: `gateway`, `worker`, `delivery` (same image, different command); reuse existing `csa_db`, `csa_embeddings`, `csa_mcp`. At your volume: **one** gateway, **one** worker (concurrency 4–8), **one** delivery worker. No Redis, no queue broker, no Kubernetes. Network `csa-net`. Ports `127.0.0.1:8080->gateway`. Healthchecks, log rotation, `read_only: true` + tmpfs where possible (matches your existing hardening). Route `csa.<your-domain>` via Cloudflare Tunnel → Caddy → gateway.

### 10. CI (GitHub Actions)
`ruff check`, `ruff format --check`, `pyright`, `pytest` with a Postgres service container (pgvector+PostGIS image to match prod), migrations applied from scratch.

## Acceptance criteria
- [ ] `docs/schema_notes.md` exists and every table has a reuse decision.
- [ ] `docker compose up` brings up gateway/worker/delivery healthy; `/readyz` green.
- [ ] RLS test passes: cross-tenant reads/writes return 0 rows / fail.
- [ ] `AsyncPostgresSaver` round-trip test passes (write a trivial 2-node graph, kill the process between nodes, resume).
- [ ] A request produces spans (console/OTLP exporter is enough) and log lines sharing the same `trace_id`.
- [ ] CI green from a clean checkout; no secrets in git history (run `gitleaks`).
- [ ] Import test proves only `csa/graph/` imports `langgraph`.

## Pitfalls
- psycopg3 is required by the checkpointer; do not mix in asyncpg.
- Don't `FORCE` RLS while n8n connects as the owner — it will go blind. Stage it (§5, LG11 M14). A role with `BYPASSRLS`/superuser (or the owner without FORCE) bypasses policies silently; run the isolation tests as `csa_app`.
- `SET LOCAL` only lasts for the transaction — always use `tenancy.tx()`; never set `app.client_id` on a pooled connection permanently.
- Do not run `setup()` on every boot from the app role.
