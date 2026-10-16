SELECT * FROM public.kb_upload_files
WHERE client_id = %s AND deleted_at IS NULL ORDER BY created_at DESC, id DESC LIMIT %s;
