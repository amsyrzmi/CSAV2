SELECT id, title, description, category, view_mode, page_count, byte_size, created_at, created_by,
       summary IS NOT NULL AS has_summary, fingerprint
FROM public.training_items
WHERE client_id = %s AND request_key = %s;
