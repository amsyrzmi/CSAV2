SELECT id, title, source_uri, category, language, version, is_active,
       file_name, chunk_count, kind, embed_model, created_at, updated_at
FROM public.kb_documents
WHERE client_id = %s
  AND (is_active = %s OR %s IS NULL)
ORDER BY created_at DESC
LIMIT %s;
