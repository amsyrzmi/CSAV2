DELETE FROM public.kb_chunks
WHERE client_id = %s
  AND document_id = %s;
