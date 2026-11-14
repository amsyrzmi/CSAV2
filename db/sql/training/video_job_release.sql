-- A job that could not finish this attempt. A transient failure goes back to queued after
-- JOB_BACKOFF_SECONDS x attempts, or fails with its code once the attempts are used up (the item
-- fails with it). Waiting for the daily processing limit (until set) goes back to queued until
-- then, gives the attempt back, and says why in error_code. Fenced on this attempt.
WITH job AS (
    UPDATE public.training_video_jobs
    SET status = CASE
            WHEN %(until)s::timestamptz IS NULL AND attempts >= %(max_attempts)s THEN 'failed'
            ELSE 'queued'
        END,
        attempts = CASE
            WHEN %(until)s::timestamptz IS NULL THEN attempts
            ELSE greatest(attempts - 1, 0)
        END,
        next_attempt_at = CASE
            WHEN %(until)s::timestamptz IS NOT NULL THEN %(until)s::timestamptz
            WHEN attempts >= %(max_attempts)s THEN next_attempt_at
            ELSE now() + make_interval(secs => %(backoff_seconds)s * attempts)
        END,
        error_code = %(code)s::text,
        locked_at = NULL
    WHERE client_id = %(client_id)s
      AND item_id = %(item_id)s
      AND stage = %(stage)s::text
      AND status = 'processing'
      AND attempts = %(attempt)s
    RETURNING client_id, item_id, status, error_code
), failed AS (
    UPDATE public.training_items i
    SET status = 'failed', failure_code = job.error_code, updated_at = now()
    FROM job
    WHERE i.client_id = job.client_id AND i.id = job.item_id AND job.status = 'failed'
)
SELECT status FROM job;
