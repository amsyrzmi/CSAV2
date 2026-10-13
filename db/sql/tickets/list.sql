SELECT id, subject, category, priority, status, sla_due_at
FROM public.tickets
WHERE client_id = %s
  AND contact_id = %s
ORDER BY id DESC
LIMIT 5;
