-- The job's end: done (the item is ready) or failed with a code (the item fails with it). Fenced
-- on this attempt.
WITH job AS (
    UPDATE public.training_video_jobs
    SET status = %(status)s::text, error_code = %(code)s::text, locked_at = NULL
    WHERE client_id = %(client_id)s
      AND item_id = %(item_id)s
      AND stage = %(stage)s::text
      AND status = 'processing'
      AND attempts = %(attempt)s
    RETURNING client_id, item_id, status, error_code
)
UPDATE public.training_items i
SET status = CASE WHEN job.status = 'done' THEN 'ready' ELSE 'failed' END,
    failure_code = job.error_code,
    updated_at = now()
FROM job
WHERE i.client_id = job.client_id AND i.id = job.item_id
RETURNING i.id, i.status;
