-- Close the open jobs that only deleted files kept alive. Params: client_id, file_ids (text[]).
-- A job qualifies when its files array is not empty, every entry carries a file_id, and each of
-- those files has a kb_upload_files row in the same client with deleted_at set (the files soft-
-- deleted earlier in this transaction count). One of the given ids must appear in the job. Same
-- predicate as migration 0550. Done jobs and jobs with a path-only entry are never touched.
UPDATE public.kb_ingest_jobs j
   SET status = 'cancelled',
       dismissed_at = now(),
       locked_at = NULL,
       finished_at = COALESCE(j.finished_at, now())
 WHERE j.client_id = %s
   AND j.status IN ('failed', 'partial', 'queued', 'processing')
   AND CASE WHEN jsonb_typeof(j.files) = 'array' THEN jsonb_array_length(j.files) ELSE 0 END > 0
   AND EXISTS (
         SELECT 1
           FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(j.files) = 'array' THEN j.files ELSE '[]'::jsonb END) AS e
          WHERE e ->> 'file_id' = ANY (%s::text[]))
   AND NOT EXISTS (
         SELECT 1
           FROM jsonb_array_elements(
                  CASE WHEN jsonb_typeof(j.files) = 'array' THEN j.files ELSE '[]'::jsonb END) AS e
          WHERE NOT EXISTS (
                  SELECT 1
                    FROM public.kb_upload_files f
                   WHERE f.client_id = j.client_id
                     AND f.id::text = e ->> 'file_id'
                     AND f.deleted_at IS NOT NULL))
RETURNING j.id;
