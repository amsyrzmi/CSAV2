-- Soft-delete the stored uploads that belong to the given jobs, so the storage sweep removes
-- their bytes. Params: client_id, job_ids (uuid[]), job_ids (uuid[]). Call under the
-- publication lock, in the transaction that takes the jobs off the list.
-- A file is deleted only when all of this holds:
--   * one of the given jobs names it in files[].file_id (every upload gets its own file id and its
--     own stored name, the same link the console uses for original_stored);
--   * it is still live and feeds no document (document_id is null): a file behind a document is
--     that document's source and goes only with the document;
--   * no other job that is still on the list names it.
-- Documents, chunks and hash tombstones are never touched.
UPDATE public.kb_upload_files f
   SET deleted_at = now()
 WHERE f.client_id = %s
   AND f.deleted_at IS NULL
   AND f.document_id IS NULL
   AND EXISTS (
         SELECT 1
           FROM public.kb_ingest_jobs j
          WHERE j.client_id = f.client_id
            AND j.id = ANY (%s::uuid[])
            AND CASE WHEN jsonb_typeof(j.files) = 'array' THEN j.files ELSE '[]'::jsonb END
                @> jsonb_build_array(jsonb_build_object('file_id', f.id::text)))
   AND NOT EXISTS (
         SELECT 1
           FROM public.kb_ingest_jobs o
          WHERE o.client_id = f.client_id
            AND o.dismissed_at IS NULL
            AND o.id <> ALL (%s::uuid[])
            AND CASE WHEN jsonb_typeof(o.files) = 'array' THEN o.files ELSE '[]'::jsonb END
                @> jsonb_build_array(jsonb_build_object('file_id', f.id::text)))
RETURNING f.id;
