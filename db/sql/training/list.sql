-- Every PDF and every ready video; with manage also the videos in any other state (uploading,
-- processing, failed), which only managers see. Job columns are for the manager's progress line.
-- A summary shows only where one can be: a PDF's, or a ready video that has speech.
SELECT i.id, i.kind, i.title, i.description, i.category, i.view_mode, i.page_count, i.byte_size,
       i.created_at, i.created_by, i.status, i.duration_ms, i.failure_code, i.video_codec,
       i.summary IS NOT NULL
           AND (i.kind = 'pdf' OR (i.status = 'ready' AND i.speech = 'speech')) AS has_summary,
       j.stage AS job_stage, j.status AS job_status, j.chunks_done, j.chunks_total,
       j.error_code AS job_error
FROM public.training_items i
LEFT JOIN public.training_video_jobs j ON j.client_id = i.client_id AND j.item_id = i.id
WHERE i.client_id = %(client_id)s
  AND (i.kind = 'pdf' OR i.status = 'ready' OR %(manage)s::boolean)
ORDER BY i.created_at DESC, i.id DESC;
