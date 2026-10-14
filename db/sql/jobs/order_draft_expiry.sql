/* db/sql/jobs/order_draft_expiry.sql */
WITH dead AS (
  SELECT c.id, c.order_id
  FROM order_confirmations c
  WHERE c.client_id = %s AND c.state = 'pending' AND c.expires_at <= now()
  ORDER BY c.expires_at
  FOR UPDATE SKIP LOCKED
  LIMIT %s
), conf AS (
  UPDATE order_confirmations c SET state = 'expired', responded_at = now()
  FROM dead d
  WHERE c.id = d.id AND c.state = 'pending'
  RETURNING c.order_id
), upd AS (
  UPDATE orders o SET status = 'cancelled', cancelled_at = now(), updated_at = now()
  FROM conf
  WHERE o.client_id = %s AND o.id = conf.order_id AND o.status = 'draft'
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
SELECT (SELECT count(*) FROM dead) AS confirmations,
       (SELECT count(*) FROM upd)  AS orders_cancelled,
       (SELECT count(*) FROM rel)  AS products_released;
