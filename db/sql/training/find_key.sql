SELECT i.id, i.kind, i.title, i.description, i.category, i.view_mode, i.page_count, i.byte_size,
       i.created_at, i.created_by, i.status, i.duration_ms, i.failure_code, i.video_codec,
       i.summary IS NOT NULL
           AND (i.kind = 'pdf' OR (i.status = 'ready' AND i.speech = 'speech')) AS has_summary,
       i.fingerprint,
       j.stage AS job_stage, j.status AS job_status, j.chunks_done, j.chunks_total,
       j.error_code AS job_error
FROM public.training_items i
LEFT JOIN public.training_video_jobs j ON j.client_id = i.client_id AND j.item_id = i.id
WHERE i.client_id = %s AND i.request_key = %s;
