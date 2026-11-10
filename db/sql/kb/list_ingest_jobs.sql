-- Visible jobs only (dismissed_at IS NULL). deleted_file_ids: the job's entries' file_ids whose
-- kb_upload_files row in the same client is soft-deleted (the console's original_stored); a
-- non-array files value or an entry without a file_id adds nothing.
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
  AND j.dismissed_at IS NULL
ORDER BY j.created_at DESC
LIMIT %s;
