-- 0493_marketplace_local_flow_guards.sql — the local draft and approval expiry functions skip synced marketplace orders.
-- Preconditions: baseline functions csa_expire_drafts() and csa_expire_pending_approvals(integer);
--   0492 applied (orders.external_order_id is the provider's order id for synced rows).
-- Why: an UNPAID TikTok order maps to local status pending_confirmation, exactly the status the
--   24 hour approval expiry cancels, and the draft expiry works from the same orders table. A synced
--   order belongs to the marketplace: only sync may change it. A synced order is a row with
--   channel IN ('tiktok_shop','shopee') and a provider order id (the predicate of
--   orders_marketplace_external_key). An order the assistant took on a TikTok chat has no provider
--   order id, is local, and still expires.
-- Rollback: CREATE OR REPLACE both functions with the bodies in db/schema_baseline.sql (lines for
--   csa_expire_drafts and csa_expire_pending_approvals): the same bodies without the marketplace
--   exclusion. No data changes in either direction; owner and EXECUTE grants are unchanged because
--   CREATE OR REPLACE keeps them.
-- RLS: both functions stay SECURITY INVOKER, so tenant isolation applies to the caller as before. No FORCE.

CREATE OR REPLACE FUNCTION public.csa_expire_drafts()
RETURNS TABLE(expired_confirmations bigint, cancelled_orders bigint)
LANGUAGE sql
AS $$
WITH dead AS (
  UPDATE order_confirmations c SET state = 'expired', responded_at = now()
  WHERE c.state = 'pending' AND c.expires_at <= now()
    AND NOT EXISTS (
      SELECT 1 FROM orders so
      WHERE so.client_id = c.client_id AND so.id = c.order_id
        AND so.channel IN ('tiktok_shop', 'shopee') AND so.external_order_id IS NOT NULL
    )
  RETURNING c.client_id, c.order_id
),
upd AS (
  UPDATE orders o SET status = 'cancelled', cancelled_at = now(), updated_at = now()
  FROM dead d WHERE o.id = d.order_id AND o.status = 'draft'
  RETURNING o.id
),
rel AS (
  UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - oi.qty, 0), updated_at = now()
  FROM order_items oi, upd WHERE oi.order_id = upd.id AND p.id = oi.product_id
  RETURNING p.id
),
ev AS (
  INSERT INTO order_events (client_id, order_id, event, actor)
  SELECT d.client_id, u.id, 'expired', 'system' FROM upd u JOIN dead d ON d.order_id = u.id
  RETURNING order_id
)
SELECT (SELECT count(*) FROM dead), (SELECT count(*) FROM upd);
$$;

CREATE OR REPLACE FUNCTION public.csa_expire_pending_approvals(p_hours integer DEFAULT 24)
RETURNS TABLE(order_id text, total numeric, currency text, customer_telegram_id bigint)
LANGUAGE sql
AS $$
WITH stale AS (
  SELECT o.id, o.client_id, o.total, o.currency, o.contact_id
  FROM orders o
  WHERE o.status = 'pending_confirmation'
    AND NOT (o.channel IN ('tiktok_shop', 'shopee') AND o.external_order_id IS NOT NULL)
    AND COALESCE(o.placed_at, o.updated_at) <= now() - make_interval(hours => p_hours)
  FOR UPDATE OF o SKIP LOCKED
),
upd AS (
  UPDATE orders o SET status = 'cancelled', cancelled_at = now(), updated_at = now()
  FROM stale s WHERE o.id = s.id
  RETURNING o.id
),
rel AS (
  UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - oi.qty, 0), updated_at = now()
  FROM order_items oi, upd WHERE oi.order_id = upd.id AND p.id = oi.product_id
  RETURNING p.id
),
ev AS (
  INSERT INTO order_events (client_id, order_id, event, actor, payload)
  SELECT s.client_id, s.id, 'cancelled', 'system',
         jsonb_build_object('reason', 'approval_timeout', 'hours', p_hours)
  FROM stale s JOIN upd u ON u.id = s.id
  RETURNING order_id
)
SELECT s.id, s.total, s.currency, ct.telegram_id
FROM stale s JOIN upd u ON u.id = s.id
LEFT JOIN contacts ct ON ct.id = s.contact_id;
$$;
