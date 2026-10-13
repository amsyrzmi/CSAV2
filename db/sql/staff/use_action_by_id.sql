UPDATE public.staff_actions
SET status = 'used', used_at = now()
WHERE client_id = %s
  AND id = %s
  AND status = 'open'
RETURNING id;
