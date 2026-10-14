# LG10 — Scheduled jobs: the remade expiry sweep, reapers, reconciliation

## CONTEXT FOR CODING AGENT
You are replacing the n8n `Expiry Sweep` (a node inside `21_order_agent`, reachable via the Router's output 7, normally called by a 5-minute cron workflow that was not in the files reviewed) and adding the other time-based invariants that nothing handles today. This phase is **isolated** (it only needs LG01–LG02: DB layer, settings, contracts), so it is the best first pilot of the new stack: small, testable, and it fixes a real stock-drift bug. Read `LG00` §2 findings #25–#27 first.

## 0. Existing database functions — read these first
Your `pg_proc` output shows four user functions that none of the workflows shown calls by name: **`csa_expire_drafts`**, **`csa_expire_pending_approvals`**, **`csa_retention`**, **`purge_ingress_raw`**. They look like the maintenance logic that was *meant* to run on a schedule (there is no `pg_cron`, so an n8n Schedule trigger or an external cron would have to call them). Their bodies arrive with `csa_schema.sql` (LG01 §1). Before writing any job:
1. **Find callers.** Export every n8n workflow and search it:
```
docker ps --format '{{.Names}}' | grep -i n8n                 # find the container name
docker exec <n8n-container> n8n export:workflow --all --output=/tmp/all_workflows.json
docker cp <n8n-container>:/tmp/all_workflows.json .
grep -c "csa_expire\|csa_retention\|purge_ingress_raw" all_workflows.json
python3 -c "import json;[print(w['name'], 'active' if w.get('active') else 'inactive') for w in json.load(open('all_workflows.json'))]"
```
   Scan for secrets before sharing anything: `grep -oE "[0-9]{8,}:[A-Za-z0-9_-]{30,}|Bearer [A-Za-z0-9]{20,}" all_workflows.json` — replace hits with `REDACTED`.
2. **Is anything expiring today?** Read-only:
```sql
SELECT count(*) AS stale_drafts FROM orders WHERE status='draft' AND created_at < now() - interval '1 day';
SELECT count(*) AS overdue_confirmations FROM order_confirmations WHERE state='pending' AND expires_at < now();
SELECT count(*) AS overdue_approvals FROM approvals WHERE status='pending' AND expires_at < now();
```
   Non-zero numbers mean nothing is running the sweep and reservations are being held by dead drafts right now.
3. **Decide per function** once you've read it:
   - `csa_expire_drafts` — if it aggregates per product, is tenant-safe and idempotent: move its SQL into `db/sql/jobs/` (so it is in git and tested) and have the runner call it per client; otherwise replace it with §3 below. Either way the *scheduling, heartbeat, alerts and tests* are new.
   - `csa_expire_pending_approvals` — a DB function cannot resume a paused LangGraph run. The `approval_expiry` job must first select the expiring approvals, resume each graph with `expire`, then mark them (calling or inlining the function's status update).
   - `csa_retention` / `purge_ingress_raw` — reuse them in the `retention` job if their bodies are sound (`purge_ingress_raw` matches `ingress_events.raw_purged_at`).
4. Keep the functions in `db/schema_baseline.sql`; drop them only after the job runner has replaced every caller.

## Why remake it (what the old sweep does wrong)
```sql
-- today (21_order_agent / Expiry Sweep), abridged
WITH dead AS (UPDATE order_confirmations SET state='expired' WHERE state='pending' AND expires_at<=now() RETURNING order_id),
     upd  AS (UPDATE orders SET status='cancelled' FROM dead WHERE orders.id=dead.order_id AND status='draft' RETURNING id),
     rel  AS (UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - oi.qty, 0)
              FROM order_items oi, upd WHERE oi.order_id=upd.id AND p.id=oi.product_id RETURNING p.id) …
```
1. **Stock leak (H).** In `UPDATE … FROM`, if several source rows match one target row, Postgres applies only one of them (which one is unspecified). With two lines of the same product, or two expiring orders for the same product in one run, only one quantity is released. `reserved_qty` drifts up forever; `GREATEST(…,0)` masks drift in the other direction. `Cancel Reservation` has the same shape.
2. **Reachable from a customer turn (H).** Router output 7 is both the `fallback` action and the switch's fallback output; `Input` doesn't validate `action`; any unknown action runs a global, all-clients sweep.
3. **Gaps (M).** `pending_confirmation` orders hold stock until staff decide, with no timeout; drafts with no confirmation row never expire; customers are never told; one statement does everything with no batching, no per-client scoping, no run log, no alert if it stops running.

## Goal
A small, observable job runner with idempotent, batched, per-client jobs — and **the invariant** `products.reserved_qty == Σ qty of order_items on orders that currently hold stock`, proven by a reconciliation job and property tests.

## 1. Job runner (`csa/jobs/`)
- A `Job(name, every, fn, timeout, per_client: bool)` registry, run by an asyncio loop in `apps/worker` (no APScheduler, no pg_cron: you want the logic in Python where it is testable).
- **One run at a time per job across replicas:** `pg_try_advisory_lock(hashtextextended(job.name,0))` on a dedicated session connection; if the lock is taken, skip the tick.
- `per_client` jobs iterate active clients (`clients.status IN ('pilot','active')`) and run each inside `tenancy.tx(client_id)` so RLS stays honest and one client's failure doesn't stop the rest.
- Every job: `SET LOCAL statement_timeout`, work in **batches** (`LIMIT n … FOR UPDATE SKIP LOCKED`), loop until the batch is empty or a time budget is hit, and be **idempotent** (re-running changes nothing).
- Time comes from an injected `Clock` so tests can jump forward instead of sleeping.
- Run log:
```sql
CREATE TABLE job_runs (
  id bigserial PRIMARY KEY, job text NOT NULL, client_id uuid,
  started_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
  ok boolean, stats jsonb NOT NULL DEFAULT '{}', error text);
CREATE INDEX job_runs_job_started ON job_runs (job, started_at DESC);
```
- **Heartbeat alert:** each job pushes a heartbeat (Uptime Kuma push monitor or a metric); alert when `now() - last_success > 3 × interval`. The old sweep could stop and nobody would know.

## 2. The jobs

| Job | Cadence | Per client | What it does |
|---|---|---|---|
| `order_draft_expiry` | 1 min | yes | Expire pending `order_confirmations` past `expires_at`; cancel the matching `draft` orders; **release reservations aggregated per product**; write `order_events` (`expired`, actor `system`); optionally notify the customer (`config.orders.notify_on_expiry`, default off) |
| `orphan_draft_expiry` | 5 min | yes | Drafts older than 2× the TTL with no pending confirmation row → same release path |
| `order_approval_watch` | 5 min | yes | `pending_confirmation` orders with no staff decision after `config.orders.staff_approval_ttl` (default 24 h): remind staff at 50 % and 100 %; auto-cancel **only** if configured |
| `reservation_reconcile` | nightly + on demand | yes | Compare `reserved_qty` with open reservations; `report` (alert) by default, `repair` only when asked (§4) |
| `approval_expiry` | 1 min | yes | Pending `approvals` past `expires_at` → resume the graph with `expire`, set status, write `approval_audit` |
| `pending_actions_expiry` | 5 min | yes | `pending_actions` `open` past `expires_at` → `expired` |
| `ingress_reaper` | 1 min | no | `ingress_events` stuck `dispatched` past the turn deadline + margin → `received` (attempts < max) else `dead` + `dlq` row |
| `outbox_reaper` | 1 min | no | `outbound_deliveries` `delivering` with stale `locked_at` → `pending` |
| `webhook_delivery_gap` | 1 min | yes | Outbound `messages` in `channel='webhook'` conversations whose endpoint has a `callback_url` but no `outbound_deliveries` row after 30 s (the trigger swallows its own errors) → create the row idempotently and alert |
| `voice_reaper` | 30 s | no | `voice_transcripts` `processing` past the deadline → `failed` + create the failed-transcript ingress event (LG03 §3F) |
| `mute_expiry` | 5 min | yes | Conversations with `owned_by='human_support'` and `ai_muted_until < now()` → release to the AI with `UPDATE conversations SET status='open', owned_by=NULL` — allowed by `keep_escalated_status` because `owned_by` changes (the trigger already lets a plain `status='open'` through once the mute has expired). `ai_muted_until IS NULL` means indefinite human ownership and is **never** auto-released |
| `ticket_sla_watch` | 5 min | yes | Tickets in `open`/`in_progress` (not `waiting_customer`) with `sla_due_at < now()` → one staff notification per ticket via the outbox idempotency key `notify:sla_breach:{ticket_id}`, plus a `ticket_events` row (`sla_breached`, actor `system`). Not `action_log` (its `conversation_id` is NOT NULL; tickets can have none). `idx_tickets_sla` only covers `status='open'` → LG11 M03 adds a wider partial index |
| `dlq_digest` | 15 min | no | Re-queue eligible `dlq` rows (`replay_attempts < max_replays`, not `abandoned`) as new `ingress_events`; alert on the rest; mark `replayed` / `abandoned` — **nothing consumes `dlq` today** |
| `budget_rollup` | hourly | yes | Tokens per client per day from `agent_runs` vs `tokens_per_day`; alert at 80 % |
| `gmail_watch_renew` | daily | yes | Renew `users.watch` for Gmail credentials (LG03 §3C) |
| `retention` | nightly | no | Purge `ingress_events.raw` (`raw_purged_at`); scrub delivered outbox `payload` (`purged_at`); null `voice_transcripts.envelope` once the job is final (it holds contact data) and apply your retention to `transcript` / `audio_uri`; prune `lg` checkpoints older than 14 days (never open approvals); trim `job_runs` / `decision_log` |

## 3. The remade order sweep (SQL)
Per client, batched, aggregated. Columns verified against your `\d+` output: `order_confirmations` has `id`, a unique `order_conf_one_pending` index on `order_id WHERE state='pending'`, and `state` CHECK `pending|confirmed|cancelled|expired`; `orders.id` and `order_items.order_id` are **text**; `products.reserved_qty` has `CHECK (reserved_qty >= 0)`.
```sql
-- db/sql/jobs/order_draft_expiry.sql        $1 client_id, $2 batch size
WITH dead AS (
  SELECT c.id, c.order_id
  FROM order_confirmations c
  WHERE c.client_id = $1 AND c.state = 'pending' AND c.expires_at <= now()
  ORDER BY c.expires_at
  FOR UPDATE SKIP LOCKED
  LIMIT $2
), conf AS (
  UPDATE order_confirmations c SET state = 'expired', responded_at = now()
  FROM dead d
  WHERE c.id = d.id AND c.state = 'pending'
  RETURNING c.order_id
), upd AS (
  UPDATE orders o SET status = 'cancelled', cancelled_at = now(), updated_at = now()
  FROM conf
  WHERE o.client_id = $1 AND o.id = conf.order_id AND o.status = 'draft'
  RETURNING o.id
), per_product AS (                                   -- ONE row per product: this is the fix
  SELECT oi.product_id, SUM(oi.qty) AS qty
  FROM order_items oi JOIN upd ON upd.id = oi.order_id
  GROUP BY oi.product_id
), rel AS (
  UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - pp.qty, 0), updated_at = now()
  FROM per_product pp
  WHERE p.client_id = $1 AND p.id = pp.product_id
  RETURNING p.id
), ev AS (
  INSERT INTO order_events (client_id, order_id, event, actor)
  SELECT $1, upd.id, 'expired', 'system' FROM upd
  RETURNING order_id
)
SELECT (SELECT count(*) FROM dead) AS confirmations,
       (SELECT count(*) FROM upd)  AS orders_cancelled,
       (SELECT count(*) FROM rel)  AS products_released;
```
- **Concurrency with the customer's Confirm:** Confirm claims `state='pending' AND expires_at > now()`; the sweep takes `state='pending' AND expires_at <= now()`. The predicates are disjoint and each row is claimed by an `UPDATE … WHERE state='pending'`, so exactly one wins. Test it (§6).
- If `rel` releases less than `GREATEST` clamps, log a **drift warning** (`stats.clamped`) instead of hiding it.
- `Cancel Reservation` in the order agent gets the same `per_product` aggregation (LG05).

## 4. Reservation reconciliation — run the read-only version today
Which order statuses hold stock is still an assumption: `orders_status_chk` allows `draft, pending_confirmation, confirmed, processing, shipped, delivered, cancelled, refunded`, and the files show reservations created at `draft` and kept at `pending_confirmation`. What happens to `stock_qty`/`reserved_qty` at `confirmed` / `processing` / `shipped` lives in the staff-approval flow, which hasn't been sent. Make the holding set configurable (`config.orders.holding_statuses`, default `draft, pending_confirmation`) and run in **report** mode until you've checked it.
```sql
WITH expected AS (
  SELECT oi.product_id, SUM(oi.qty) AS qty
  FROM order_items oi JOIN orders o ON o.id = oi.order_id
  WHERE o.client_id = $1 AND o.status IN ('draft','pending_confirmation')
  GROUP BY oi.product_id
)
SELECT p.id, p.sku, p.reserved_qty AS actual, COALESCE(e.qty,0) AS expected,
       p.reserved_qty - COALESCE(e.qty,0) AS drift
FROM products p LEFT JOIN expected e ON e.product_id = p.id
WHERE p.client_id = $1 AND p.reserved_qty <> COALESCE(e.qty,0)
ORDER BY abs(p.reserved_qty - COALESCE(e.qty,0)) DESC;
```
- Mode `report` (default): write `job_runs.stats` + alert when any drift exists. Mode `repair`: `UPDATE products SET reserved_qty = expected` for the drifted rows inside one transaction, with the before/after recorded in `job_runs.stats`; never automatic until you trust the report.
- Once the new sweep is live, **drift should stay at zero**; any non-zero result is a bug report, not routine noise.

## 5. Migration and cutover of the sweep
1. **Today:** run §4 (read-only) on production to measure existing drift; if non-zero, plan a one-off `repair` after pausing the n8n cron.
2. Implement the job + tests (§6). Run it in **dry-run** (SELECT-only variant) beside the n8n cron for a few days and compare counts each tick.
3. Switch: disable the n8n cron, enable the job (`repair` still off). Watch `job_runs` and heartbeat for a week.
4. Remove the Router fallback → sweep wiring (or retire `21_order_agent` entirely at LG05/LG09); ensure an unknown order action is now a validation error.

## 6. Tests (all offline, injected `Clock`)
- **Property test (the important one):** generate random sequences of create-draft / cancel / confirm / expire with duplicate SKU lines inside one draft, split-line oversell attempts and many orders per product; after each step assert `reserved_qty == Σ open reservations` (the reconciliation SQL returns no rows).
- 1,000 expired drafts processed in batches; re-running changes nothing (idempotent).
- Confirm vs sweep race on the same order (concurrent tasks): exactly one outcome, stock consistent.
- Per-client isolation: client A's sweep never touches client B (RLS on, wrong `app.client_id` sees nothing).
- Runner: lock prevents double-run across two runner instances; a crashing job logs `ok=false` and the next tick still runs; heartbeat stops → alert fires.
- Each reaper: seed a stuck row, advance the clock, assert the exact transition and the `dlq` row where applicable.

## Acceptance criteria
- [x] `job_runs` shows every job running on schedule with advisory locking and stats; a stopped job reports unhealthy past 3 intervals (`is_healthy`).
- [x] Property test passes (`test_property_stock_reservation_integrity`) verifying zero stock drift invariant across order creation, confirmation, and sweep.
- [x] Multi-line per-product stock aggregation verified (`test_order_draft_expiry_per_product_aggregation`), preventing the legacy n8n stock leak.
- [x] Reservation reconciliation report and repair verified (`test_reservation_reconcile_and_repair`).
- [x] Orphan draft expiry (`test_orphan_draft_expiry`), pending actions reaper, and mute reaper verified with clock injection.
- [x] Scheduled jobs loop wired into `apps/worker/main.py`.
- [ ] Production reconciliation report (read-only) has been run and its result recorded in `docs/` (upon live production access).
- [ ] n8n expiry cron disabled only after the dry-run comparison matched for several days.

## Pitfalls
- Don't "fix" drift by trusting `GREATEST(…,0)`; aggregate, then reconcile.
- Don't run jobs from the customer-turn path or inside the graph; they are time-driven and tenant-iterating.
- Batch size and statement timeouts matter more than cadence: a sweep that can lock the `products` table for seconds will stall order creation.
- Auto-cancelling `pending_confirmation` orders is a business decision, not a technical one — leave it off until the client agrees.
