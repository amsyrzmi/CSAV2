/* db/sql/jobs/reservation_repair.sql */
WITH expected AS (
  SELECT oi.product_id, SUM(oi.qty) AS qty
  FROM order_items oi JOIN orders o ON o.id = oi.order_id
  WHERE o.client_id = %s AND o.status = ANY(%s::text[])
  GROUP BY oi.product_id
), drifted AS (
  SELECT p.id, p.sku, p.reserved_qty AS old_reserved, COALESCE(e.qty, 0) AS new_reserved
  FROM products p LEFT JOIN expected e ON e.product_id = p.id
  WHERE p.client_id = %s AND p.reserved_qty <> COALESCE(e.qty, 0)
  FOR UPDATE OF p
), upd AS (
  UPDATE products p
  SET reserved_qty = d.new_reserved, updated_at = now()
  FROM drifted d
  WHERE p.id = d.id AND p.client_id = %s
  RETURNING p.id, d.sku, d.old_reserved, d.new_reserved
)
SELECT id, sku, old_reserved, new_reserved FROM upd;
