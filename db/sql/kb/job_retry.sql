-- Queue a failed or partial upload job again, every file of it. Params: client_id, job_id.
-- One guarded UPDATE, so two quick calls queue once. created_at moves to now so the deletion
-- fence (kb_deletions.deleted_at >= created_at) compares against the retry, not the first queue.
-- files and meta stay as they are: the worker answers duplicate for files already read, and its
-- finish replaces meta.results.
UPDATE public.kb_ingest_jobs
   SET status = 'queued',
       attempts = 0,
       error = NULL,
       created_at = clock_timestamp(),
       started_at = NULL,
       finished_at = NULL,
       locked_at = NULL
 WHERE client_id = %s
   AND id = %s
   AND source = 'upload'
   AND status IN ('failed', 'partial')
   AND dismissed_at IS NULL
RETURNING id;
