SELECT id
FROM public.tickets
WHERE client_id = %s
  AND conversation_id = %s
  AND category = 'escalation'
  AND status NOT IN ('cancelled', 'closed', 'resolved')
ORDER BY id DESC
LIMIT 1;
