-- One chunk transcribed: chunks_done moves from %(done)s to %(done)s + 1 only when it is still
-- %(done)s at this attempt, in the same transaction as the chunk's segments, so a chunk is
-- committed once. It refreshes the heartbeat and records the language the provider reported for
-- the chunk in parts ({"chunk", "language"}; the transcript's language is the most frequent one,
-- and parts is emptied when transcribing ends). No row back: the job is not this attempt's.
UPDATE public.training_video_jobs
SET chunks_done = %(done)s + 1,
    chunks_total = %(chunks_total)s,
    locked_at = now(),
    parts = CASE
        WHEN %(language)s::text IS NULL THEN parts
        ELSE parts || jsonb_build_array(
            jsonb_build_object('chunk', %(done)s::int, 'language', %(language)s::text))
    END
WHERE client_id = %(client_id)s
  AND item_id = %(item_id)s
  AND stage = 'transcribing'
  AND status = 'processing'
  AND attempts = %(attempt)s
  AND chunks_done = %(done)s
RETURNING chunks_done;
