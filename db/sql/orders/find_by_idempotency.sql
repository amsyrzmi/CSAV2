SELECT id, status, total, currency
FROM public.orders
WHERE client_id = %s
  AND idempotency_key = %s;
