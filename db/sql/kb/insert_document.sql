INSERT INTO public.kb_documents
    (id, client_id, title, source_uri, category, language, is_active, file_name, content_hash,
     chunk_count, kind, embed_model, version)
VALUES
    (%s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, %s, %s)
RETURNING id, client_id, title, version, is_active, created_at;
