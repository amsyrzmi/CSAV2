-- Take a finished job off the list. Params: client_id, job_id. No row: the job is absent,
-- already dismissed, or still queued or processing (the route tells these apart).
UPDATE public.kb_ingest_jobs
   SET dismissed_at = now()
 WHERE client_id = %s
   AND id = %s
   AND dismissed_at IS NULL
   AND status IN ('failed', 'partial', 'cancelled', 'done')
RETURNING id;
