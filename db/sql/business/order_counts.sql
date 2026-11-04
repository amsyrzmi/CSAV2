SELECT status, count(*) AS n
FROM public.orders
WHERE client_id = %s
  AND (%s::text IS NULL OR channel = %s)
  AND (%s::text IS NULL OR external_shop_id = %s)
  AND (%s::timestamptz IS NULL OR created_at >= %s)
  AND (%s::timestamptz IS NULL OR created_at < %s)
  AND (%s::text IS NULL OR payment_status = %s)
GROUP BY status;
