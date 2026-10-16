UPDATE public.kb_upload_files SET document_id = %s
WHERE client_id = %s AND id = %s AND deleted_at IS NULL;
