SELECT id, status, payment_status, total, currency, placed_at, shipped_at, tracking_no,
       carrier, created_at, items
FROM public.orders
WHERE client_id = %s
  AND contact_id = %s
  AND (%s::text IS NULL OR id = %s)
  AND (NOT %s OR status IN ('pending_confirmation', 'confirmed', 'processing', 'shipped'))
ORDER BY created_at DESC
LIMIT 5;
