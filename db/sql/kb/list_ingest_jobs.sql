SELECT id, source, status, files, meta, requested_by, attempts, error,
       created_at, started_at, finished_at
FROM public.kb_ingest_jobs
WHERE client_id = %s
ORDER BY created_at DESC
LIMIT %s;
