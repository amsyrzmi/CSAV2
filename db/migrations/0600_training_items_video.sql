-- Video in the training library, part 1: training_items can hold a video as well as a PDF.
-- Changes to public.training_items (0541):
--   kind: the auto-named check (kind IN ('pdf')) is dropped (found in pg_constraint as the one check
--     on the kind column alone, not by its generated name) and training_items_kind_chk allows
--     'pdf' and 'video'.
--   view_mode, page_count, sha256: NOT NULL dropped. A video has no view mode or page count, and a
--     video still uploading has no digest yet. The old checks on view_mode and page_count stay
--     (a NULL passes a CHECK).
--   New columns: status (uploading, uploaded, transcribing, summarising, ready, failed; default
--     'ready', so every PDF row already there reads as ready), duration_ms, container (mp4, webm),
--     speech (speech, no_speech, no_audio), transcript_language, failure_code, video_codec,
--     audio_codec.
--   New checks: one per new column, training_items_shape_chk (a PDF has view_mode and page_count and
--     no container; a video has neither view_mode nor page_count and has a container),
--     training_items_pdf_ready_chk (a PDF is always ready) and training_items_sha256_chk (sha256 may
--     be NULL only while status is uploading). On this populated table each is added NOT VALID, then
--     validated: every existing row is a PDF with status ready, so validation passes.
--   UNIQUE (client_id, id): the target of the composite foreign keys in 0601.
-- Row security and grants are unchanged (ENABLE only, never FORCE). Safe to run twice: columns use
--   IF NOT EXISTS, constraints are checked by name in pg_constraint, DROP NOT NULL repeats cleanly.
-- Pre-check: 0581 applied and 0541 applied. Every training_items row must have kind = 'pdf':
--   SELECT count(*) FROM public.training_items WHERE kind <> 'pdf'; must be 0, and
--   SELECT count(*) FROM public.training_items WHERE view_mode IS NULL OR page_count IS NULL
--   OR sha256 IS NULL; must be 0. ADD COLUMN with a constant default does not rewrite the table.
--   Lock: the whole file is one transaction, so the ACCESS EXCLUSIVE lock the ALTERs take (the
--   unique index build and each NOT VALID check then its VALIDATE included) is held until commit;
--   brief on this small table, but every training read and write waits for it.
-- Rollback (after 0601 is rolled back; as the database owner):
--   DELETE FROM public.training_items WHERE kind = 'video'; (the stored video files stay in the
--   training store with no row; the store's sweep removes them; the console_audit rows stay)
--   ALTER TABLE public.training_items DROP CONSTRAINT training_items_sha256_chk,
--     DROP CONSTRAINT training_items_pdf_ready_chk, DROP CONSTRAINT training_items_shape_chk,
--     DROP CONSTRAINT training_items_audio_codec_chk, DROP CONSTRAINT training_items_video_codec_chk,
--     DROP CONSTRAINT training_items_failure_code_chk,
--     DROP CONSTRAINT training_items_transcript_language_chk,
--     DROP CONSTRAINT training_items_speech_chk, DROP CONSTRAINT training_items_container_chk,
--     DROP CONSTRAINT training_items_duration_chk, DROP CONSTRAINT training_items_status_chk,
--     DROP CONSTRAINT training_items_kind_chk, DROP CONSTRAINT training_items_client_id_id_key;
--   ALTER TABLE public.training_items DROP COLUMN audio_codec, DROP COLUMN video_codec,
--     DROP COLUMN failure_code, DROP COLUMN transcript_language, DROP COLUMN speech,
--     DROP COLUMN container, DROP COLUMN duration_ms, DROP COLUMN status;
--   ALTER TABLE public.training_items ALTER COLUMN view_mode SET NOT NULL,
--     ALTER COLUMN page_count SET NOT NULL, ALTER COLUMN sha256 SET NOT NULL;
--   ALTER TABLE public.training_items ADD CONSTRAINT training_items_kind_check
--     CHECK (kind IN ('pdf'));
--   Finally delete the schema_migrations row for 0600, or the runner thinks it is still applied.

ALTER TABLE public.training_items
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'ready',
    ADD COLUMN IF NOT EXISTS duration_ms int,
    ADD COLUMN IF NOT EXISTS container text,
    ADD COLUMN IF NOT EXISTS speech text,
    ADD COLUMN IF NOT EXISTS transcript_language text,
    ADD COLUMN IF NOT EXISTS failure_code text,
    ADD COLUMN IF NOT EXISTS video_codec text,
    ADD COLUMN IF NOT EXISTS audio_codec text;

ALTER TABLE public.training_items
    ALTER COLUMN view_mode DROP NOT NULL,
    ALTER COLUMN page_count DROP NOT NULL,
    ALTER COLUMN sha256 DROP NOT NULL;

DO $$
DECLARE
  spec text[];
  old_check name;
BEGIN
  -- New checks, each added NOT VALID when its name is missing, then validated below.
  FOREACH spec SLICE 1 IN ARRAY ARRAY[
      ['training_items_kind_chk', 'CHECK (kind IN (''pdf'', ''video''))'],
      ['training_items_status_chk',
       'CHECK (status IN (''uploading'', ''uploaded'', ''transcribing'', ''summarising'', ''ready'', ''failed''))'],
      ['training_items_duration_chk', 'CHECK (duration_ms > 0)'],
      ['training_items_container_chk', 'CHECK (container IN (''mp4'', ''webm''))'],
      ['training_items_speech_chk', 'CHECK (speech IN (''speech'', ''no_speech'', ''no_audio''))'],
      ['training_items_transcript_language_chk', 'CHECK (char_length(transcript_language) <= 16)'],
      ['training_items_failure_code_chk', 'CHECK (char_length(failure_code) <= 64)'],
      ['training_items_video_codec_chk', 'CHECK (char_length(video_codec) <= 32)'],
      ['training_items_audio_codec_chk', 'CHECK (char_length(audio_codec) <= 32)'],
      ['training_items_shape_chk',
       'CHECK ((kind = ''pdf'' AND view_mode IS NOT NULL AND page_count IS NOT NULL AND container IS NULL)'
       ' OR (kind = ''video'' AND view_mode IS NULL AND page_count IS NULL AND container IS NOT NULL))'],
      ['training_items_pdf_ready_chk', 'CHECK (kind <> ''pdf'' OR status = ''ready'')'],
      ['training_items_sha256_chk', 'CHECK (sha256 IS NOT NULL OR status = ''uploading'')']
  ] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                    WHERE conrelid = 'public.training_items'::regclass AND conname = spec[1]) THEN
      EXECUTE format('ALTER TABLE public.training_items ADD CONSTRAINT %I %s NOT VALID', spec[1], spec[2]);
    END IF;
  END LOOP;

  -- The old kind check is the only check on the kind column alone; 0541 left it auto-named.
  FOR old_check IN
    SELECT c.conname
      FROM pg_catalog.pg_constraint c
      JOIN pg_catalog.pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
     WHERE c.conrelid = 'public.training_items'::regclass AND c.contype = 'c'
       AND pg_catalog.array_length(c.conkey, 1) = 1 AND a.attname = 'kind'
       AND c.conname <> 'training_items_kind_chk'
  LOOP
    EXECUTE format('ALTER TABLE public.training_items DROP CONSTRAINT %I', old_check);
  END LOOP;

  -- Validation scans the table once per constraint, and does nothing for one already validated.
  FOR old_check IN
    SELECT conname FROM pg_catalog.pg_constraint
     WHERE conrelid = 'public.training_items'::regclass AND contype = 'c' AND NOT convalidated
  LOOP
    EXECUTE format('ALTER TABLE public.training_items VALIDATE CONSTRAINT %I', old_check);
  END LOOP;

  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
                  WHERE conrelid = 'public.training_items'::regclass
                    AND conname = 'training_items_client_id_id_key') THEN
    ALTER TABLE public.training_items
        ADD CONSTRAINT training_items_client_id_id_key UNIQUE (client_id, id);
  END IF;
END $$;
