INSERT INTO public.kb_deletions(client_id, content_hash, deleted_at)
VALUES (%s, %s, clock_timestamp())
ON CONFLICT (client_id, content_hash) DO UPDATE SET deleted_at = clock_timestamp();
