UPDATE public.kb_gaps
SET resolved = true
WHERE client_id = %s
  AND id = %s
RETURNING id, query, resolved;
