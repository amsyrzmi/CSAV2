# LG11 — Schema change inventory (ordered, additive, safe for a live n8n)

## CONTEXT FOR CODING AGENT
All DDL the rebuild needs, in one place and in the order to apply it. The phase files (LG01–LG10) describe *why*; this file is the authoritative *what*. If SQL here and in a phase file ever differ, **this file wins** — fix the phase file. Everything is based on the `\d+` / `\sf` output you sent; anything not shown is marked *verify*.

## Rules for every migration
1. **n8n keeps running.** Until cutover (LG09) n8n connects as the table owner `csa` and knows nothing about new columns. Only *additive* or *loosening* changes until M14.
2. Idempotent where possible (`IF NOT EXISTS`). One file per migration in `db/migrations/` using your next free numbers (`M01` … below are labels, not file numbers).
3. Constraints on existing tables: `ADD CONSTRAINT … NOT VALID` → fix/check data → `VALIDATE CONSTRAINT` (no long locks).
4. Indexes: `CREATE INDEX CONCURRENTLY` / `DROP INDEX CONCURRENTLY`, which cannot run inside a transaction — the migration runner must run those statements in autocommit mode.
5. Run as the owner `csa`. Secrets (role passwords) come from the deploy script, never from git.
6. Each migration lists a pre-check and a rollback note. Take a `pg_dump` before M01 and M10.

## Order at a glance
| # | Migration | Needed by | Risk |
|---|---|---|---|
| M01 | Roles + staged RLS + `resolve_endpoint()` | LG01, LG03 | medium (test n8n after) |
| M02 | Drop duplicate indexes | housekeeping | low |
| M03 | CHECKs and helper indexes | LG03, LG05, LG10 | low–medium (data pre-checks) |
| M04 | `capability_registry` additions | LG02, LG05 | low |
| M05 | `outbound_deliveries` outbox columns | LG07 | low |
| M06 | `approvals` unique per trace | LG07 | low (pre-check duplicates) |
| M07 | `job_runs` | LG10 | low |
| M08 | `staff_notification_targets` | LG05 | low |
| M09 | `decision_log` | LG06 | low |
| M10 | `new_order_id()` | LG05 | low |
| M11 | Backfill `contact_identities` | LG03 | low |
| M12 | `wa_templates` | LG03 §3B | low |
| M13 | Re-run the RLS block for new tables | after M07–M09, M12 | low |
| M14 | **Contract** (after n8n core is retired): FORCE RLS, drop `ingress_dedup`, remove dual-writes | LG09 | medium |

---

## M01 — Roles, staged RLS, endpoint lookup
```sql
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='csa_app') THEN CREATE ROLE csa_app LOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='csa_ro')  THEN CREATE ROLE csa_ro  LOGIN; END IF;
END $$;
-- passwords: ALTER ROLE csa_app PASSWORD '…' from the deploy script (secret)
GRANT USAGE ON SCHEMA public TO csa_app, csa_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO csa_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO csa_app;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO csa_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO csa_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO csa_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO csa_ro;

-- RLS block (idempotent; re-run as M13 after new tenant tables are created)
DO $$
DECLARE t text;
BEGIN
  FOR t IN
    SELECT c.table_name FROM information_schema.columns c
    JOIN pg_tables p ON p.schemaname = c.table_schema AND p.tablename = c.table_name   -- base tables only (no views)
    WHERE c.table_schema = 'public' AND c.column_name = 'client_id'
  LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);   -- ENABLE, not FORCE (see M14)
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON public.%I
        USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
        WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid)$p$, t);
  END LOOP;
END $$;

-- the only pre-tenant lookup the gateway may do
CREATE OR REPLACE FUNCTION public.resolve_endpoint(p_public_id text)
RETURNS TABLE (endpoint_id uuid, client_id uuid, credential_id uuid, channel text,
               reply_mode text, sync_wait_ms int, callback_url text, rate_limit_rpm int, is_enabled boolean)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public, pg_temp AS $$
  SELECT e.id, e.client_id, e.credential_id, cc.channel, e.reply_mode, e.sync_wait_ms,
         e.callback_url, e.rate_limit_rpm, e.is_enabled
  FROM webhook_endpoints e JOIN channel_credentials cc ON cc.id = e.credential_id
  WHERE e.public_id = p_public_id
$$;
REVOKE ALL ON FUNCTION public.resolve_endpoint(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.resolve_endpoint(text) TO csa_app;
```
- **Why not FORCE yet:** the owner `csa` (n8n, migrations, and your `SECURITY DEFINER` trigger functions) must keep bypassing. The new stack uses `csa_app`, which is subject to the policies.
- **Pre-check:** confirm which DB role n8n's credential uses (`SELECT usename FROM pg_stat_activity WHERE application_name ILIKE '%n8n%';` or inspect the credential). If it is not the owner, `ENABLE` alone would blind it — grant it a bypass policy first.
- **Test after:** an n8n execution of `01_ingress` still works; as `csa_app` with no `app.client_id` every tenant table returns 0 rows; with client A set, client B's rows are invisible.
- **Rollback:** `ALTER TABLE … DISABLE ROW LEVEL SECURITY` per table; drop policies/roles.

## M02 — Drop duplicate indexes (housekeeping)
```sql
-- verify the pairs really are identical first
SELECT indexname, indexdef FROM pg_indexes WHERE tablename='products'
  AND indexname IN ('products_name_trgm','products_name_trgm_idx','products_cat_idx','products_category_idx');
DROP INDEX CONCURRENTLY IF EXISTS products_name_trgm_idx;   -- keep products_name_trgm
DROP INDEX CONCURRENTLY IF EXISTS products_cat_idx;         -- keep products_category_idx
```
Rollback: recreate from the `indexdef` you saved.

## M03 — CHECK constraints and helper indexes
```sql
-- pre-checks: adjust the allowed lists to what your data and the ticket agent's constants actually use
SELECT DISTINCT status FROM tickets;           SELECT DISTINCT priority FROM tickets;   SELECT DISTINCT category FROM tickets;
SELECT DISTINCT status FROM voice_transcripts; SELECT DISTINCT status FROM pending_actions;
SELECT DISTINCT circuit_state FROM capability_registry;

ALTER TABLE tickets
  ADD CONSTRAINT tickets_status_chk   CHECK (status   IN ('open','in_progress','waiting_customer','resolved','closed','cancelled')) NOT VALID,
  ADD CONSTRAINT tickets_priority_chk CHECK (priority IN ('urgent','high','normal','low')) NOT VALID,
  ADD CONSTRAINT tickets_category_chk CHECK (category IN ('returns','shipping','product','billing','technical','other','escalation')) NOT VALID;
ALTER TABLE tickets VALIDATE CONSTRAINT tickets_status_chk;
ALTER TABLE tickets VALIDATE CONSTRAINT tickets_priority_chk;
ALTER TABLE tickets VALIDATE CONSTRAINT tickets_category_chk;

-- WhatsApp / email orders (Create Draft currently hard-codes 'telegram')
BEGIN;
  ALTER TABLE orders DROP CONSTRAINT orders_channel_chk;
  ALTER TABLE orders ADD CONSTRAINT orders_channel_chk
    CHECK (channel IN ('telegram','whatsapp','email','web','tiktok_shop','shopee','manual')) NOT VALID;
COMMIT;
ALTER TABLE orders VALIDATE CONSTRAINT orders_channel_chk;

ALTER TABLE voice_transcripts ADD CONSTRAINT voice_transcripts_status_chk
  CHECK (status IN ('processing','completed','failed','timeout')) NOT VALID;
ALTER TABLE pending_actions ADD CONSTRAINT pending_actions_status_chk
  CHECK (status IN ('open','used','expired','cancelled')) NOT VALID;
ALTER TABLE capability_registry ADD CONSTRAINT capability_registry_circuit_state_chk
  CHECK (circuit_state IN ('closed','open','half_open')) NOT VALID;
-- VALIDATE each after the pre-check queries return only allowed values

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_voice_transcripts_processing ON voice_transcripts (created_at) WHERE status = 'processing';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_pending_actions_open        ON pending_actions (expires_at)  WHERE status = 'open';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_conversations_muted         ON conversations (ai_muted_until) WHERE owned_by = 'human_support';
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tickets_sla_active          ON tickets (sla_due_at) WHERE status IN ('open','in_progress');
```
Rollback: `DROP CONSTRAINT` / `DROP INDEX`; restore the old `orders_channel_chk` (`telegram, web, tiktok_shop, shopee, manual`).

## M04 — `capability_registry` additions (LG02 §5)
```sql
ALTER TABLE capability_registry
  ADD COLUMN IF NOT EXISTS impl_kind text NOT NULL DEFAULT 'n8n',   -- n8n | python | mcp | http | n8n_webhook
  ADD COLUMN IF NOT EXISTS impl_ref text,
  ADD COLUMN IF NOT EXISTS routing_hint text;
ALTER TABLE capability_registry ALTER COLUMN workflow_id DROP NOT NULL;   -- loosening: n8n only reads it
```
Existing rows keep `impl_kind='n8n'`, so the old coordinator is unaffected. Do **not** add `timeout_ms` / `max_retries` — they exist.

## M05 — Outbox columns on `outbound_deliveries` (LG07)
```sql
ALTER TABLE outbound_deliveries
  ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'webhook',   -- existing/trigger rows are webhook callbacks
  ADD COLUMN IF NOT EXISTS channel text,
  ADD COLUMN IF NOT EXISTS contact_id uuid REFERENCES contacts(id),
  ADD COLUMN IF NOT EXISTS payload jsonb,                          -- neutral OutboundMessage; scrubbed after delivery
  ADD COLUMN IF NOT EXISTS idempotency_key text,
  ADD COLUMN IF NOT EXISTS provider_message_id text,
  ADD COLUMN IF NOT EXISTS purged_at timestamptz;
ALTER TABLE outbound_deliveries ADD CONSTRAINT outbound_deliveries_kind_chk
  CHECK (kind IN ('reply','notification','webhook')) NOT VALID;
ALTER TABLE outbound_deliveries VALIDATE CONSTRAINT outbound_deliveries_kind_chk;
ALTER TABLE outbound_deliveries ALTER COLUMN message_id DROP NOT NULL;    -- staff notifications have no customer message
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS outbound_deliveries_idem_uidx
  ON outbound_deliveries (client_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
```
`enqueue_webhook_delivery()` inserts `(client_id, endpoint_id, trace_id, conversation_id, message_id, next_attempt_at)`; `kind` defaults to `'webhook'`, so the trigger keeps working unchanged. The existing `UNIQUE (message_id)` stays (NULLs are allowed more than once).

## M06 — One approval per turn
```sql
SELECT trace_id, count(*) FROM approvals GROUP BY 1 HAVING count(*) > 1;   -- must return nothing
CREATE UNIQUE INDEX CONCURRENTLY approvals_trace_uidx ON approvals (trace_id);
```

## M07 — `job_runs` (LG10)
```sql
CREATE TABLE IF NOT EXISTS job_runs (
  id bigserial PRIMARY KEY, job text NOT NULL, client_id uuid,
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  ok boolean, stats jsonb NOT NULL DEFAULT '{}', error text);
CREATE INDEX IF NOT EXISTS job_runs_job_started ON job_runs (job, started_at DESC);
```

## M08 — `staff_notification_targets` (LG05)
```sql
CREATE TABLE IF NOT EXISTS staff_notification_targets (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES clients(id),
  channel text NOT NULL CHECK (channel IN ('telegram','email','whatsapp','webhook')),
  address text NOT NULL,
  events text[] NOT NULL DEFAULT '{escalation,approval_needed,order_submitted,dlq,circuit_opened}',
  min_priority text NOT NULL DEFAULT 'normal',
  quiet_hours jsonb, is_enabled boolean NOT NULL DEFAULT true, verified_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (client_id, channel, address));
-- seed your own client with the chat id currently hard-coded in 21/30/40 (do the INSERT by hand; don't commit the id)
```

## M09 — `decision_log` (LG06)
```sql
CREATE TABLE IF NOT EXISTS decision_log (
  id bigserial PRIMARY KEY, client_id uuid NOT NULL REFERENCES clients(id), trace_id uuid NOT NULL, node text NOT NULL,
  provider text NOT NULL, model_version text, latency_ms int,
  state_hash text, answers jsonb NOT NULL, final jsonb,
  human_outcome text,
  created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS decision_log_client_created ON decision_log (client_id, created_at DESC);
```

## M10 — Order ids
```sql
CREATE OR REPLACE FUNCTION public.new_order_id() RETURNS text LANGUAGE sql VOLATILE AS
$$ SELECT 'ORD-' || upper(substr(md5(gen_random_uuid()::text || clock_timestamp()::text), 1, 10)) $$;
```
`orders.id` stays `text` (it is referenced by five tables). 10 hex chars = 40 bits (≈50 % collision only around a million orders); `create_draft` still **retries on a primary-key conflict**. Old 8-character ids keep working; lookups accept 8–12 characters.

## M11 — Backfill `contact_identities` (LG03)
```sql
-- the tenant-check trigger requires the contact to exist and match the client (it does)
INSERT INTO contact_identities (client_id, channel, external_id, contact_id)
SELECT client_id, 'telegram', telegram_id::text, id FROM contacts WHERE telegram_id IS NOT NULL
UNION ALL SELECT client_id, 'email', lower(email), id FROM contacts WHERE email IS NOT NULL
UNION ALL SELECT client_id, 'web_console', console_user_id::text, id FROM contacts WHERE console_user_id IS NOT NULL
ON CONFLICT DO NOTHING;
```
`contacts.phone` is **not** backfilled as a WhatsApp identity: it is unverified and unnormalized. Link phones through `contact_verifications` (OTP) or create them on first WhatsApp contact (normalized to E.164). Keep dual-writing the legacy columns while any client runs on n8n.

## M12 — `wa_templates` (LG03 §3B)
```sql
CREATE TABLE IF NOT EXISTS wa_templates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  client_id uuid NOT NULL REFERENCES clients(id),
  name text NOT NULL, language text NOT NULL, category text, body text NOT NULL,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','paused')),
  meta_template_id text, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (client_id, name, language));
```

## M13 — Re-run the M01 RLS block
New tenant tables (`job_runs` has `client_id`, `staff_notification_targets`, `decision_log`, `wa_templates`) need policies; the block is idempotent.

## M14 — Contract (only after the n8n core is retired — LG09)
```sql
-- 1) move n8n/legacy clients off the owner role (or delete them), then:
--    re-run M01's DO block with FORCE:  ALTER TABLE public.<t> FORCE ROW LEVEL SECURITY;
-- 2) DROP TABLE ingress_dedup;                      -- superseded by ingress_events' unique key
-- 3) remove dual-writes to contacts.telegram_id / email / console_user_id in the app (columns stay for display)
-- 4) revisit: keep trg_webhook_delivery (it owns webhook enqueueing); drop only if replaced by explicit outbox rows
```
Never drop `enqueue_webhook_delivery`, `keep_escalated_status`, `contact_identity_tenant_check`, or `trg_sync_order_items` as part of this rebuild — they encode rules the new code relies on.

---

## What deliberately does *not* change
- `messages` (keys get an endpoint prefix in code, LG02; the table is untouched), `conversations`, `contacts`, `orders` (id stays `text`; `items` stays trigger-maintained), `approvals` (apart from M06), `dlq`, `agent_runs`, `action_log`.
- No new queue table: `ingress_events` is the queue; `outbound_deliveries` is the outbox.
- No schema for LangGraph checkpoints here: `AsyncPostgresSaver.setup()` creates its own tables in schema `lg` (LG01 §7), run by the owner.

## Acceptance criteria
- [ ] M01–M13 apply cleanly to a copy of production, in order, and are re-runnable.
- [ ] After M01, a full n8n `01_ingress → … → 30_egress` round trip still works; the `csa_app` isolation tests pass.
- [ ] After M05, a webhook-channel message still produces exactly one trigger-created delivery with `kind='webhook'`.
- [ ] `VALIDATE CONSTRAINT` succeeded for every `NOT VALID` constraint (or the offending rows were fixed deliberately).
- [ ] A CI job builds the schema from `db/migrations/` plus the seed and runs the full golden suite against it.
