-- The Payments and Connections tabs in the staff assistant: console_workspace_turns_page_key_check
--   (the tab a turn was asked on; 0460, extended in 0531 and 0540) also allows 'payments' and
--   'connections', so a turn asked on those tabs can be stored. Every page already allowed is kept.
-- Why: the Overview assistant change (PR 3a) added both tabs to the assistant's TabKey list without a
--   migration, so on a database at 0610 a turn asked on either tab would fail the check.
-- How: as 0540. The allowed values are read from the constraint as the database stores it
--   (pg_get_constraintdef), the two keys are added when missing, and the constraint is built again
--   from that list. No page of another track is named here, so one this migration has never heard of
--   is not lost. When both values are already there the constraint is left alone, so running the file
--   again changes nothing.
-- No tables, columns, policies, grants or roles change. FORCE RLS stays off.
-- Pre-check: the constraint exists and is a plain list of quoted values (0460, 0531, 0540); the
--   migration stops with an error rather than guess if it is missing or reads differently.
-- Rollback: DELETE FROM public.console_workspace_turns WHERE page_key IN ('payments', 'connections');
--   then drop the constraint and add it again with the list it held before (read it with
--   pg_get_constraintdef and leave the two values out). Finally delete the schema_migrations row for
--   0611, or the runner thinks it is still applied.

DO $$
DECLARE
  def text;
  allowed text[];
  wanted text[] := ARRAY['payments', 'connections'];
  missing text[];
BEGIN
  SELECT pg_catalog.pg_get_constraintdef(c.oid) INTO def
    FROM pg_catalog.pg_constraint c
   WHERE c.conrelid = 'public.console_workspace_turns'::regclass
     AND c.conname = 'console_workspace_turns_page_key_check';
  IF def IS NULL THEN
    RAISE EXCEPTION '0611: console_workspace_turns_page_key_check is missing on public.console_workspace_turns';
  END IF;
  IF def LIKE '%''''%' OR def !~ '^CHECK \(\(page_key = ANY \(ARRAY\[' THEN
    RAISE EXCEPTION '0611: console_workspace_turns_page_key_check does not read as a plain list of values: %', def;
  END IF;
  SELECT array_agg(m[1] ORDER BY n) INTO allowed
    FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
  IF allowed IS NULL THEN
    RAISE EXCEPTION '0611: console_workspace_turns_page_key_check lists no values: %', def;
  END IF;
  SELECT array_agg(w ORDER BY n) INTO missing
    FROM unnest(wanted) WITH ORDINALITY AS u(w, n)
   WHERE NOT w = ANY (allowed);
  IF missing IS NOT NULL THEN
    ALTER TABLE public.console_workspace_turns DROP CONSTRAINT console_workspace_turns_page_key_check;
    EXECUTE format('ALTER TABLE public.console_workspace_turns'
                   ' ADD CONSTRAINT console_workspace_turns_page_key_check CHECK (page_key IN (%s))',
                   (SELECT string_agg(quote_literal(v), ', ' ORDER BY n)
                      FROM unnest(allowed || missing) WITH ORDINALITY AS u(v, n)));
  END IF;
END $$;
