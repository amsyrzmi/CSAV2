-- One visible job (dismissed_at IS NULL), with deleted_file_ids as in list_ingest_jobs.sql.
SELECT j.id, j.source, j.status, j.files, j.meta, j.requested_by, j.attempts, j.error,
       j.created_at, j.started_at, j.finished_at,
       ARRAY(
         SELECT f.id
           FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(j.files) = 'array' THEN j.files ELSE '[]'::jsonb END) AS e
           JOIN public.kb_upload_files f
             ON f.client_id = j.client_id
            AND f.id::text = e ->> 'file_id'
          WHERE f.deleted_at IS NOT NULL) AS deleted_file_ids
FROM public.kb_ingest_jobs j
WHERE j.client_id = %s
  AND j.id = %s
  AND j.dismissed_at IS NULL;
