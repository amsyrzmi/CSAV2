-- One item with its stored fields; the thumbnail as its version only (as list.sql).
SELECT i.id, i.kind, i.title, i.description, i.category, i.view_mode, i.page_count, i.byte_size,
       i.created_at, i.created_by, i.status, i.duration_ms, i.failure_code, i.video_codec,
       i.summary IS NOT NULL
           AND (i.kind = 'pdf' OR (i.status = 'ready' AND i.speech = 'speech')) AS has_summary,
       i.storage_name, i.original_name, i.media_type, i.sha256, i.container, i.speech,
       i.transcript_language, t.sha256 AS thumbnail_version
FROM public.training_items i
LEFT JOIN public.media_assets t
       ON t.client_id = i.client_id AND t.kind = 'training_thumbnail' AND t.subject_key = i.id::text
WHERE i.client_id = %s AND i.id = %s;
