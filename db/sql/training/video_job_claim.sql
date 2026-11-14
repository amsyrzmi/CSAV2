-- Claim the company's oldest due job, only while none of its jobs is processing (one job of a
-- company in flight; the worker is a single process). The job becomes processing with one more
-- attempt and a fresh heartbeat, its waiting code is cleared, and the item shows the job's stage.
WITH job AS (
    UPDATE public.training_video_jobs j
    SET status = 'processing', attempts = j.attempts + 1, locked_at = now(), error_code = NULL
    WHERE (j.client_id, j.item_id) = (
        SELECT q.client_id, q.item_id
        FROM public.training_video_jobs q
        WHERE q.client_id = %(client_id)s
          AND q.status = 'queued'
          AND q.next_attempt_at <= now()
          AND NOT EXISTS (
              SELECT 1 FROM public.training_video_jobs p
              WHERE p.client_id = %(client_id)s AND p.status = 'processing'
          )
        ORDER BY q.created_at, q.item_id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING j.client_id, j.item_id, j.stage, j.attempts, j.chunks_done, j.chunks_total, j.parts
), item AS (
    UPDATE public.training_items i
    SET status = job.stage, updated_at = now()
    FROM job
    WHERE i.client_id = job.client_id AND i.id = job.item_id
    RETURNING i.id, i.storage_name, i.container, i.duration_ms, i.speech
)
SELECT job.client_id, job.item_id, job.stage, job.attempts, job.chunks_done, job.chunks_total,
       job.parts, item.storage_name, item.container, item.duration_ms, item.speech
FROM job
JOIN item ON item.id = job.item_id;
