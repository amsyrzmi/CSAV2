SELECT id, title, chunk_count, version
FROM public.kb_documents
WHERE client_id = %s
  AND content_hash = %s
  AND is_active = true
LIMIT 1;
