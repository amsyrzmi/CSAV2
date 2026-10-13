SELECT id, status, payment_status, total, currency, placed_at, shipped_at, tracking_no,
       carrier, contact_id, created_at
FROM public.orders
WHERE client_id = %s
  AND id = %s
  AND contact_id = %s
FOR UPDATE;
