-- What the probe found, stored in the job's fenced transaction once the file passed the checks:
-- the duration and the first video and audio streams' codecs (the list's HEVC notice reads
-- video_codec). A video with no audio stream is decided here, in the same statement: speech
-- 'no_audio', so a later attempt never needs to probe again to know it. The job learns how many
-- chunks it will take.
WITH job AS (
    UPDATE public.training_video_jobs
    SET chunks_total = %(chunks_total)s
    WHERE client_id = %(client_id)s
      AND item_id = %(item_id)s
      AND stage = 'transcribing'
      AND status = 'processing'
      AND attempts = %(attempt)s
    RETURNING client_id, item_id
)
UPDATE public.training_items i
SET duration_ms = %(duration_ms)s,
    video_codec = %(video_codec)s::text,
    audio_codec = %(audio_codec)s::text,
    speech = CASE WHEN %(audio_codec)s::text IS NULL THEN 'no_audio' ELSE i.speech END,
    updated_at = now()
FROM job
WHERE i.client_id = job.client_id AND i.id = job.item_id
RETURNING i.id, i.speech;
