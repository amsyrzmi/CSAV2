UPDATE public.kb_ingest_jobs
SET status = %s,
    error = %s,
    meta = meta || %s::jsonb,
    finished_at = now(),
    locked_at = NULL
WHERE client_id = %s
  AND id = %s
  AND status = 'processing'
RETURNING id, status;
