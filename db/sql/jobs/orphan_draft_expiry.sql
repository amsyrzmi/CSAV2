/* db/sql/jobs/orphan_draft_expiry.sql */
WITH dead AS (
  SELECT o.id
  FROM orders o
  WHERE o.client_id = %s
    AND o.status = 'draft'
    AND o.created_at <= %s
    AND NOT EXISTS (
      SELECT 1 FROM order_confirmations c
      WHERE c.client_id = %s AND c.order_id = o.id AND c.state = 'pending'
    )
  ORDER BY o.created_at
  FOR UPDATE SKIP LOCKED
  LIMIT %s
), upd AS (
  UPDATE orders o SET status = 'cancelled', cancelled_at = now(), updated_at = now()
  FROM dead d
  WHERE o.client_id = %s AND o.id = d.id AND o.status = 'draft'
  RETURNING o.id
), per_product AS (
  SELECT oi.product_id, SUM(oi.qty) AS qty
  FROM order_items oi JOIN upd ON upd.id = oi.order_id
  GROUP BY oi.product_id
), rel AS (
  UPDATE products p SET reserved_qty = GREATEST(p.reserved_qty - pp.qty, 0), updated_at = now()
  FROM per_product pp
  WHERE p.client_id = %s AND p.id = pp.product_id
  RETURNING p.id
), ev AS (
  INSERT INTO order_events (client_id, order_id, event, actor)
  SELECT %s, upd.id, 'expired', 'system' FROM upd
  RETURNING order_id
)
SELECT (SELECT count(*) FROM dead) AS orders_cancelled,
       (SELECT count(*) FROM rel)  AS products_released;
