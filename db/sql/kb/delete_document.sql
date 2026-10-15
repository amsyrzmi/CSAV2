DELETE FROM public.kb_documents
WHERE client_id = %s
  AND id = %s
RETURNING id;
