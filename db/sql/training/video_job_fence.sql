-- The attempt fence, first in every transaction a job writes in: lock this attempt's job row and
-- refresh its heartbeat. The stage is part of the fence: attempts restart at 1 for the summary, so
-- the attempt number alone would let a stale transcribing attempt write into it. No row back: the
-- item was removed, the job moved on to the next stage, or it went stale and was claimed again by a
-- later attempt; the caller writes nothing.
UPDATE public.training_video_jobs
SET locked_at = now()
WHERE client_id = %(client_id)s
  AND item_id = %(item_id)s
  AND stage = %(stage)s::text
  AND status = 'processing'
  AND attempts = %(attempt)s
RETURNING attempts;
