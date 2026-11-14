-- Jobs whose worker stopped beating (killed, or stuck past JOB_STALE_SECONDS): back to queued, or
-- failed with the stage's retryable code once its attempts are used up, and then the item fails
-- with the same code. A requeued job keeps chunks_done and parts, so it resumes where it stopped.
-- Run in the company's transaction before its claim.
WITH stale AS (
    UPDATE public.training_video_jobs
    SET status = CASE WHEN attempts >= %(max_attempts)s THEN 'failed' ELSE 'queued' END,
        error_code = CASE
            WHEN attempts < %(max_attempts)s THEN error_code
            WHEN stage = 'transcribing' THEN 'training_transcription_unavailable'
            ELSE 'training_summary_unavailable'
        END,
        locked_at = NULL,
        next_attempt_at = now()
    WHERE client_id = %(client_id)s
      AND status = 'processing'
      AND locked_at < now() - make_interval(secs => %(stale_seconds)s)
    RETURNING client_id, item_id, stage, status, error_code
), failed AS (
    UPDATE public.training_items i
    SET status = 'failed', failure_code = stale.error_code, updated_at = now()
    FROM stale
    WHERE i.client_id = stale.client_id AND i.id = stale.item_id AND stale.status = 'failed'
)
SELECT item_id, stage, status FROM stale;
