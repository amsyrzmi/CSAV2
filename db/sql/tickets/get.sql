SELECT id, subject, category, priority, status, sla_due_at, created_at, closed_at
FROM public.tickets
WHERE client_id = %s
  AND contact_id = %s
  AND id = %s;
