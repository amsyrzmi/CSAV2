SELECT id, client_id
FROM public.approvals
WHERE status = 'pending'
  AND expires_at <= now()
ORDER BY expires_at
LIMIT 50;
