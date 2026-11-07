SELECT id, title, description, category, view_mode, page_count, byte_size, created_at, created_by,
       summary IS NOT NULL AS has_summary
FROM public.training_items
WHERE client_id = %s
ORDER BY created_at DESC, id DESC;
