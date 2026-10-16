SELECT id, title,
       CASE WHEN source_uri LIKE 'upload:%%' THEN NULL ELSE source_uri END AS source_uri,
       category, language, version, is_active,
       file_name, chunk_count, kind, embed_model, created_at, updated_at
FROM public.kb_documents
WHERE client_id = %s
  AND (is_active = %s OR %s::boolean IS NULL)
ORDER BY created_at DESC
LIMIT %s;
