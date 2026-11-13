-- What transcribing decided about the video: speech (with the transcript's language) or
-- no_speech. Run in the job's fenced transaction; the job's parts (the per-chunk languages) are
-- emptied for the summary, which keeps its own parts there.
WITH job AS (
    UPDATE public.training_video_jobs
    SET parts = '[]'::jsonb
    WHERE client_id = %(client_id)s
      AND item_id = %(item_id)s
      AND stage = 'transcribing'
      AND status = 'processing'
      AND attempts = %(attempt)s
    RETURNING client_id, item_id
)
UPDATE public.training_items i
SET speech = %(speech)s::text,
    transcript_language = %(language)s::text,
    updated_at = now()
FROM job
WHERE i.client_id = job.client_id AND i.id = job.item_id
RETURNING i.id;
