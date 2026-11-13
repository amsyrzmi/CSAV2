-- A NULL parameter leaves its column as it is. The caller refuses a view mode for a video first.
WITH changed AS (
    UPDATE public.training_items
    SET title = coalesce(%(title)s::text, title),
        description = coalesce(%(description)s::text, description),
        category = coalesce(%(category)s::text, category),
        view_mode = coalesce(%(view_mode)s::text, view_mode),
        updated_at = now()
    WHERE client_id = %(client_id)s AND id = %(id)s
    RETURNING *
)
SELECT c.id, c.kind, c.title, c.description, c.category, c.view_mode, c.page_count, c.byte_size,
       c.created_at, c.created_by, c.status, c.duration_ms, c.failure_code, c.video_codec,
       c.summary IS NOT NULL
           AND (c.kind = 'pdf' OR (c.status = 'ready' AND c.speech = 'speech')) AS has_summary,
       j.stage AS job_stage, j.status AS job_status, j.chunks_done, j.chunks_total,
       j.error_code AS job_error
FROM changed c
LEFT JOIN public.training_video_jobs j ON j.client_id = c.client_id AND j.item_id = c.id;
