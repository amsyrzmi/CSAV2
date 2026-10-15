-- Jobs abandoned mid-processing (worker crash) are retried until attempts run out.
UPDATE public.kb_ingest_jobs
SET status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END,
    error = CASE WHEN attempts >= %s THEN 'processing abandoned; attempts exhausted' ELSE error END,
    finished_at = CASE WHEN attempts >= %s THEN now() ELSE finished_at END,
    locked_at = NULL
WHERE client_id = %s
  AND status = 'processing'
  AND locked_at < now() - make_interval(secs => %s)
RETURNING id, status;
