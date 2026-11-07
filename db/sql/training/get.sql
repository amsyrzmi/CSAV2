SELECT id, title, description, category, view_mode, page_count, byte_size, created_at, created_by,
       summary IS NOT NULL AS has_summary, storage_name, original_name, media_type, sha256
FROM public.training_items
WHERE client_id = %s AND id = %s;
