UPDATE public.staff_actions
SET status = 'used', used_at = now()
WHERE client_id = %s
  AND order_id = %s
  AND status = 'open'
RETURNING id;
