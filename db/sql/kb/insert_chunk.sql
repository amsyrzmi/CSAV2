INSERT INTO public.kb_chunks
    (id, client_id, document_id, chunk_index, content, heading, embedding, token_count, question)
VALUES
    (%s, %s, %s, %s, %s, %s, %s::vector, %s, %s)
RETURNING id;
