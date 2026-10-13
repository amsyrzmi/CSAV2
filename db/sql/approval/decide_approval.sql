UPDATE public.approvals
SET status = %s, decided_by = %s, decided_at = now(), final_text = %s
WHERE client_id = %s
  AND id = %s
  AND status = 'pending'
RETURNING id;
