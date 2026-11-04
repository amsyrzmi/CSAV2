-- Index for the business orders list and per-status counts (db/sql/business/list_orders.sql,
-- db/sql/business/order_counts.sql): both filter by client_id and a created_at window, and the
-- list sorts and keyset-pages on (created_at, id). Only idx_orders_client (client_id) and
-- orders_recent_idx (client_id, contact_id, placed_at) existed, so every query read the whole
-- tenant and sorted it. Scratch Postgres, 600k rows, tenant of 120k: unfiltered list 581 ms ->
-- 0.6 ms, 30-day window list 123 ms -> 0.9 ms, 30-day counts 329 ms -> 68 ms.
-- Pre-check: orders exists (baseline). Safe to run twice: CREATE INDEX IF NOT EXISTS. Not
--   CONCURRENTLY: the migration runner applies each file inside a transaction.
-- Rollback: DROP INDEX public.orders_client_created_idx;

CREATE INDEX IF NOT EXISTS orders_client_created_idx
    ON public.orders (client_id, created_at);
