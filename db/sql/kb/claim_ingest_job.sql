UPDATE public.kb_ingest_jobs
SET status = 'processing',
    attempts = attempts + 1,
    started_at = COALESCE(started_at, now()),
    locked_at = now()
WHERE id = (
    SELECT id
    FROM public.kb_ingest_jobs j
    WHERE client_id = %s
      AND source = 'upload'
      AND status = 'queued'
      AND (locked_at IS NULL OR locked_at <= now())
      AND EXISTS (
          SELECT 1 FROM public.capability_registry r
          WHERE r.client_id = j.client_id AND r.name = 'kb_agent'
            AND r.is_enabled AND r.client_enabled
      )
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
RETURNING id, client_id, source, status, files, meta, requested_by, attempts, created_at;
