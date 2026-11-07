-- The training library's tab in the staff assistant: console_workspace_turns_page_key_check (the tab
--   a turn was asked on, 0460, extended in 0531) also allows 'training_library', so a turn asked on
--   that tab can be stored. Every page already allowed is kept.
-- How: as 0531. The allowed values are read from the constraint as the database stores it
--   (pg_get_constraintdef), 'training_library' is added when it is missing, and the constraint is
--   built again from that list. No page of another track is named here, so one this migration has
--   never heard of is not lost. When the value is already there the constraint is left alone, so
--   running the file again changes nothing.
-- No tables, columns, policies, grants or roles change. FORCE RLS stays off.
-- Pre-check: the constraint exists and is a plain list of quoted values (0460, 0531); the migration
--   stops with an error rather than guess if it is missing or reads differently.
-- Rollback: DELETE FROM public.console_workspace_turns WHERE page_key = 'training_library'; then drop
--   the constraint and add it again with the list it held before (read it with pg_get_constraintdef
--   and leave 'training_library' out). Finally delete the schema_migrations row for 0540, or the
--   runner thinks it is still applied.

DO $$
DECLARE
  def text;
  allowed text[];
BEGIN
  SELECT pg_catalog.pg_get_constraintdef(c.oid) INTO def
    FROM pg_catalog.pg_constraint c
   WHERE c.conrelid = 'public.console_workspace_turns'::regclass
     AND c.conname = 'console_workspace_turns_page_key_check';
  IF def IS NULL THEN
    RAISE EXCEPTION '0540: console_workspace_turns_page_key_check is missing on public.console_workspace_turns';
  END IF;
  IF def LIKE '%''''%' OR def !~ '^CHECK \(\(page_key = ANY \(ARRAY\[' THEN
    RAISE EXCEPTION '0540: console_workspace_turns_page_key_check does not read as a plain list of values: %', def;
  END IF;
  SELECT array_agg(m[1] ORDER BY n) INTO allowed
    FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
  IF allowed IS NULL THEN
    RAISE EXCEPTION '0540: console_workspace_turns_page_key_check lists no values: %', def;
  END IF;
  IF NOT 'training_library' = ANY (allowed) THEN
    ALTER TABLE public.console_workspace_turns DROP CONSTRAINT console_workspace_turns_page_key_check;
    EXECUTE format('ALTER TABLE public.console_workspace_turns'
                   ' ADD CONSTRAINT console_workspace_turns_page_key_check CHECK (page_key IN (%s))',
                   (SELECT string_agg(quote_literal(v), ', ' ORDER BY n)
                      FROM unnest(allowed || ARRAY['training_library']) WITH ORDINALITY AS u(v, n)));
  END IF;
END $$;
