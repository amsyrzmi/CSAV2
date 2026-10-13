UPDATE public.order_confirmations
SET state = %s,
    responded_at = now()
WHERE client_id = %s
  AND order_id = %s
  AND state = 'pending'
RETURNING id;
