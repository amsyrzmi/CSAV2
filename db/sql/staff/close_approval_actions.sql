UPDATE public.staff_actions
SET status = 'used', used_at = now()
WHERE client_id = %s
  AND approval_id = %s
  AND status IN ('open', 'awaiting');
