SELECT id, status
FROM public.tickets
WHERE client_id = %s
  AND contact_id = %s
  AND conversation_id IS NOT DISTINCT FROM %s
  AND lower(subject) = lower(%s)
  AND status NOT IN ('cancelled', 'closed', 'resolved')
  AND created_at > now() - interval '10 minutes'
ORDER BY id DESC
LIMIT 1;
