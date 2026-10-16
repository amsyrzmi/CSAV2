UPDATE public.kb_upload_files SET deleted_at = COALESCE(deleted_at, now())
WHERE client_id = %s AND id = %s RETURNING *;
