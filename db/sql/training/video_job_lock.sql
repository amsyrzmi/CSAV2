-- Removal takes the video's job row before the item, in the order the worker's fenced
-- transactions take them (job, then item), so a removal waits for a running write instead of
-- deadlocking with it. No row for a PDF or a video without a job: nothing is locked.
SELECT item_id
FROM public.training_video_jobs
WHERE client_id = %(client_id)s
  AND item_id = %(item_id)s
FOR UPDATE;
