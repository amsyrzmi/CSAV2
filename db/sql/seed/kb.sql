WITH document AS (
    INSERT INTO public.kb_documents (id, client_id, title, category, language, kind, embed_model, chunk_count)
    VALUES (%(document_id)s, %(client_id)s, %(title)s, %(category)s, 'en', 'doc', 'hash-384', 1)
    ON CONFLICT (id) DO NOTHING
    RETURNING id
)
INSERT INTO public.kb_chunks (id, client_id, document_id, chunk_index, content, heading, embedding, token_count)
SELECT %(chunk_id)s, %(client_id)s, id, 0, %(content)s, %(title)s, %(embedding)s::vector, 64
FROM document
ON CONFLICT (id) DO NOTHING;
