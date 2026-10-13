SELECT id, subject, status, closed_at
FROM public.tickets
WHERE client_id = %s
  AND contact_id = %s
  AND id = %s
FOR UPDATE;
