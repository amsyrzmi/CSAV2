-- Cancel a queued job that no worker holds yet, and take it off the list. Params: client_id,
-- job_id. A job a worker has claimed is processing and is not matched.
UPDATE public.kb_ingest_jobs j
   SET status = 'cancelled',
       dismissed_at = now(),
       locked_at = NULL,
       finished_at = COALESCE(j.finished_at, now())
 WHERE j.client_id = %s
   AND j.id = %s
   AND j.status = 'queued'
   AND j.dismissed_at IS NULL
RETURNING j.id, j.source, j.status, j.files, j.meta, j.requested_by, j.attempts, j.error,
          j.created_at, j.started_at, j.finished_at,
          ARRAY(
            SELECT f.id
              FROM jsonb_array_elements(
                     CASE WHEN jsonb_typeof(j.files) = 'array' THEN j.files ELSE '[]'::jsonb END)
                   AS e
              JOIN public.kb_upload_files f
                ON f.client_id = j.client_id
               AND f.id::text = e ->> 'file_id'
             WHERE f.deleted_at IS NOT NULL) AS deleted_file_ids;
