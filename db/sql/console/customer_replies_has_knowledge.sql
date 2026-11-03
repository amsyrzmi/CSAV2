SELECT EXISTS (
    SELECT 1
    FROM public.kb_chunks c
    JOIN public.kb_documents d ON d.id = c.document_id AND d.client_id = c.client_id
    WHERE c.client_id = %s AND d.client_id = %s AND d.is_active
) AS has_knowledge;
