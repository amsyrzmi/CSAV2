-- Knowledge cleanup: kb_ingest_jobs can say a job was cancelled and can hide a job from the list.
--   * kb_ingest_jobs_status_check (queued, processing, done, partial, failed) also allows 'cancelled':
--     the end of a job whose source was removed before it could finish.
--   * kb_ingest_jobs.dismissed_at (timestamptz, NULL): set when the owner takes a job's message off
--     the list, or when the system settles a job nobody can act on. NULL means the job is visible.
--   * One-time data fix: a job in failed, partial, queued or processing whose files array is not
--     empty, where every entry carries a file_id and each of those files has a kb_upload_files row
--     with deleted_at set, can never be tried again, so it becomes 'cancelled' and dismissed (locked_at
--     cleared, finished_at filled when empty). A job with one live file, with an entry that has a path
--     and no file_id (a legacy job), with a file_id that has no kb_upload_files row, or with no files
--     at all is left exactly as it is, as is any done job.
-- How: as 0540. The allowed statuses are read from the constraint as the database stores it
--   (pg_get_constraintdef), 'cancelled' is added when it is missing, and the constraint is built again
--   from that list, so a status this migration has never heard of is not lost. When the status is
--   already there the constraint is left alone. The column is added IF NOT EXISTS, and the data fix
--   only touches jobs that are still in one of the four open statuses, so running the file again
--   changes nothing.
-- No tables, policies, grants or roles change. FORCE RLS stays off.
-- Pre-check: the constraint exists and is a plain list of quoted values (baseline); the migration
--   stops with an error rather than guess if it is missing or reads differently.
-- Rollback: first move the cancelled jobs back, UPDATE public.kb_ingest_jobs SET status = 'failed'
--   WHERE status = 'cancelled'; then drop kb_ingest_jobs_status_check and add it again with the list
--   it held before (read it with pg_get_constraintdef and leave 'cancelled' out), then ALTER TABLE
--   public.kb_ingest_jobs DROP COLUMN dismissed_at. The backfill itself is not reversible (the old
--   status of each job is not kept); the jobs it touched could not be tried again. Finally delete the
--   schema_migrations row for 0550, or the runner thinks it is still applied.

ALTER TABLE public.kb_ingest_jobs ADD COLUMN IF NOT EXISTS dismissed_at timestamptz;

DO $$
DECLARE
  def text;
  allowed text[];
BEGIN
  SELECT pg_catalog.pg_get_constraintdef(c.oid) INTO def
    FROM pg_catalog.pg_constraint c
   WHERE c.conrelid = 'public.kb_ingest_jobs'::regclass
     AND c.conname = 'kb_ingest_jobs_status_check';
  IF def IS NULL THEN
    RAISE EXCEPTION '0550: kb_ingest_jobs_status_check is missing on public.kb_ingest_jobs';
  END IF;
  IF def LIKE '%''''%' OR def !~ '^CHECK \(\(status = ANY \(ARRAY\[' THEN
    RAISE EXCEPTION '0550: kb_ingest_jobs_status_check does not read as a plain list of values: %', def;
  END IF;
  SELECT array_agg(m[1] ORDER BY n) INTO allowed
    FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
  IF allowed IS NULL THEN
    RAISE EXCEPTION '0550: kb_ingest_jobs_status_check lists no values: %', def;
  END IF;
  IF NOT 'cancelled' = ANY (allowed) THEN
    ALTER TABLE public.kb_ingest_jobs DROP CONSTRAINT kb_ingest_jobs_status_check;
    EXECUTE format('ALTER TABLE public.kb_ingest_jobs'
                   ' ADD CONSTRAINT kb_ingest_jobs_status_check CHECK (status IN (%s))',
                   (SELECT string_agg(quote_literal(v), ', ' ORDER BY n)
                      FROM unnest(allowed || ARRAY['cancelled']) WITH ORDINALITY AS u(v, n)));
  END IF;
END $$;

UPDATE public.kb_ingest_jobs j
   SET status = 'cancelled',
       dismissed_at = now(),
       locked_at = NULL,
       finished_at = COALESCE(j.finished_at, now())
 WHERE j.status IN ('failed', 'partial', 'queued', 'processing')
   AND jsonb_typeof(j.files) = 'array'
   AND jsonb_array_length(j.files) > 0
   AND NOT EXISTS (
         SELECT 1
           FROM jsonb_array_elements(j.files) AS e
          WHERE NOT EXISTS (
                  SELECT 1
                    FROM public.kb_upload_files f
                   WHERE f.client_id = j.client_id
                     AND f.id::text = e ->> 'file_id'
                     AND f.deleted_at IS NOT NULL));
