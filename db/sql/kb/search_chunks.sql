SELECT c.id,
       c.document_id,
       c.chunk_index,
       c.content,
       c.heading,
       c.question,
       d.title,
       d.source_uri,
       1 - (c.embedding <=> %s::vector) AS similarity
FROM public.kb_chunks c
JOIN public.kb_documents d ON d.id = c.document_id AND d.client_id = c.client_id
WHERE c.client_id = %s
  AND d.is_active = true
  AND d.embed_model = %s
ORDER BY c.embedding <=> %s::vector
LIMIT %s;
