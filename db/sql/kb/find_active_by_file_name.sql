SELECT id, version
FROM public.kb_documents
WHERE client_id = %s
  AND file_name = %s
  AND is_active = true
ORDER BY version DESC
FOR UPDATE;
