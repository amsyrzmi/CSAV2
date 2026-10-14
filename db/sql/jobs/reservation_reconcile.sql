/* db/sql/jobs/reservation_reconcile.sql */
WITH expected AS (
  SELECT oi.product_id, SUM(oi.qty) AS qty
  FROM order_items oi JOIN orders o ON o.id = oi.order_id
  WHERE o.client_id = %s AND o.status = ANY(%s::text[])
  GROUP BY oi.product_id
)
SELECT p.id, p.sku, p.reserved_qty AS actual, COALESCE(e.qty, 0) AS expected,
       p.reserved_qty - COALESCE(e.qty, 0) AS drift
FROM products p LEFT JOIN expected e ON e.product_id = p.id
WHERE p.client_id = %s AND p.reserved_qty <> COALESCE(e.qty, 0)
ORDER BY abs(p.reserved_qty - COALESCE(e.qty, 0)) DESC;
