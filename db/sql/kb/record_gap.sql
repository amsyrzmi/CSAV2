INSERT INTO public.kb_gaps (client_id, query, hits, first_seen, last_seen, resolved)
VALUES (%s, %s, 1, now(), now(), false)
ON CONFLICT (client_id, query_hash) DO UPDATE
SET hits = public.kb_gaps.hits + 1,
    last_seen = now(),
    resolved = false
RETURNING id, query, hits, first_seen, last_seen, resolved;
