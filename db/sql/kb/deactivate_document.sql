UPDATE public.kb_documents
SET is_active = false,
    updated_at = now()
WHERE client_id = %s
  AND id = %s;
