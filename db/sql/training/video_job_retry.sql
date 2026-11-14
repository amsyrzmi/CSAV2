-- A manager's retry of a video that failed for a reason worth trying again (codes): its job goes
-- back to queued at the stage that failed, attempts reset and codes cleared, and the item shows
-- that stage again. No row back: no such item, not a failed video, a permanent refusal, or no
-- failed job. Returns the item in the shape of list.sql.
WITH job AS (
    UPDATE public.training_video_jobs j
    SET status = 'queued', attempts = 0, error_code = NULL, locked_at = NULL,
        next_attempt_at = now()
    FROM public.training_items i
    WHERE j.client_id = %(client_id)s
      AND j.item_id = %(item_id)s
      AND j.status = 'failed'
      AND i.client_id = j.client_id
      AND i.id = j.item_id
      AND i.kind = 'video'
      AND i.status = 'failed'
      AND i.failure_code = ANY(%(codes)s::text[])
    RETURNING j.client_id, j.item_id, j.stage, j.status, j.chunks_done, j.chunks_total,
              j.error_code
), item AS (
    UPDATE public.training_items i
    SET status = job.stage, failure_code = NULL, updated_at = now()
    FROM job
    WHERE i.client_id = job.client_id AND i.id = job.item_id
    RETURNING i.*
)
SELECT item.id, item.kind, item.title, item.description, item.category, item.view_mode,
       item.page_count, item.byte_size, item.created_at, item.created_by, item.status,
       item.duration_ms, item.failure_code, item.video_codec,
       item.summary IS NOT NULL
           AND (item.kind = 'pdf' OR (item.status = 'ready' AND item.speech = 'speech'))
           AS has_summary,
       job.stage AS job_stage, job.status AS job_status, job.chunks_done, job.chunks_total,
       job.error_code AS job_error
FROM item
JOIN job ON job.client_id = item.client_id AND job.item_id = item.id;
