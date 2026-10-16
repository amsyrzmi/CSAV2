SELECT content_hash FROM public.kb_deletions
WHERE client_id = %s AND content_hash = %s AND deleted_at >= %s;
