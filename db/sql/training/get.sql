SELECT id, kind, title, description, category, view_mode, page_count, byte_size, created_at,
       created_by, status, duration_ms, failure_code, video_codec,
       summary IS NOT NULL
           AND (kind = 'pdf' OR (status = 'ready' AND speech = 'speech')) AS has_summary,
       storage_name, original_name, media_type, sha256, container, speech, transcript_language
FROM public.training_items
WHERE client_id = %s AND id = %s;
