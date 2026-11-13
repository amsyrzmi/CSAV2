-- Transcribing done with speech: the job moves to summarising, queued, with its attempts reset
-- (each stage gets its own), and the item shows summarising. Fenced on this attempt.
WITH job AS (
    UPDATE public.training_video_jobs
    SET stage = 'summarising', status = 'queued', attempts = 0, locked_at = NULL,
        next_attempt_at = now(), error_code = NULL
    WHERE client_id = %(client_id)s
      AND item_id = %(item_id)s
      AND stage = 'transcribing'
      AND status = 'processing'
      AND attempts = %(attempt)s
    RETURNING client_id, item_id
)
UPDATE public.training_items i
SET status = 'summarising', updated_at = now()
FROM job
WHERE i.client_id = job.client_id AND i.id = job.item_id
RETURNING i.id;
