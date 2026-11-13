-- The attempt fence, first in every transaction a job writes in: lock this attempt's job row and
-- refresh its heartbeat. No row back: the item was removed, or the job went stale and was claimed
-- again by a later attempt; the caller writes nothing.
UPDATE public.training_video_jobs
SET locked_at = now()
WHERE client_id = %(client_id)s
  AND item_id = %(item_id)s
  AND status = 'processing'
  AND attempts = %(attempt)s
RETURNING attempts;
