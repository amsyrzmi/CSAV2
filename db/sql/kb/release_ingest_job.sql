-- Transient failure (embedding service down): requeue with a not-before time in
-- locked_at so the job backs off, or fail once attempts are exhausted.
UPDATE public.kb_ingest_jobs
SET status = CASE WHEN attempts >= %s THEN 'failed' ELSE 'queued' END,
    error = %s,
    finished_at = CASE WHEN attempts >= %s THEN now() ELSE NULL END,
    locked_at = CASE WHEN attempts >= %s THEN NULL ELSE now() + make_interval(secs => %s) END
WHERE client_id = %s
  AND id = %s
  AND status = 'processing'
RETURNING id, status;
