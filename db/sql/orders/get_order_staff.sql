SELECT id, status, payment_status, total, currency, contact_id
FROM public.orders
WHERE client_id = %s
  AND id = %s
FOR UPDATE;
