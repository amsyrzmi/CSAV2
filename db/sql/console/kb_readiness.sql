SELECT
    count(*) FILTER (WHERE d.embed_model = %s) AS compatible_chunks,
    count(*) FILTER (WHERE d.embed_model IS DISTINCT FROM %s) AS incompatible_chunks
FROM public.kb_chunks c
JOIN public.kb_documents d ON d.id = c.document_id AND d.client_id = c.client_id
WHERE c.client_id = %s AND d.is_active;
