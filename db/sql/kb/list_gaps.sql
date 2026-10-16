SELECT id, query, hits, first_seen, last_seen, resolved
FROM public.kb_gaps
WHERE client_id = %s
  AND (resolved = %s OR %s::boolean IS NULL)
ORDER BY hits DESC, last_seen DESC
LIMIT %s;
