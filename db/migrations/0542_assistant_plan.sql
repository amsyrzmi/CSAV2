-- The staff assistant's plan card and batch email: console_proposals_kind_check also allows 'plan'
--   (one card, several steps, one confirm) and console_workspace_turns_action_check also allows
--   'plan' and 'email_batch' (the planner's two new actions). Every kind and action already
--   allowed is kept. public.email_drafts also gets facts_used (jsonb, nullable): the recipient and
--   the facts a drafted email used, so the owner can read them. A plan card and a turn row hold
--   counts and step titles only; no address, name, subject or body goes on either.
-- How: as 0531. The allowed values are read from each constraint as the database stores it
--   (pg_get_constraintdef), a value is added when it is missing, and the constraint is built again
--   from that list. No kind or action of another track is named here, so one this migration has
--   never heard of is not lost. When every value this file adds is already there the constraint is
--   left alone, and the column is added only when missing, so running the file again changes
--   nothing.
-- No data changes. No policies, grants or roles change (email_drafts keeps its owner-only row
--   security from 0430). FORCE RLS stays off.
-- Pre-check: both constraints exist and are plain lists of quoted values (0521, 0531); the migration
--   stops with an error rather than guess if one is missing or reads differently.
-- Rollback: DELETE FROM public.console_proposals WHERE kind = 'plan'; DELETE FROM
--   public.console_workspace_turns WHERE action IN ('plan', 'email_batch'); ALTER TABLE
--   public.email_drafts DROP COLUMN facts_used; then drop both constraints and add them again with
--   the lists they held before (read them with pg_get_constraintdef and leave the values above
--   out). Finally delete the schema_migrations row for 0542, or the runner thinks it is still
--   applied.

DO $$
DECLARE
  spec text[];
  def text;
  allowed text[];
  wanted text[];
BEGIN
  -- Each spec: table, constraint, column, and the values this file adds (comma separated).
  FOREACH spec SLICE 1 IN ARRAY ARRAY[
      ARRAY['console_proposals', 'console_proposals_kind_check', 'kind', 'plan'],
      ARRAY['console_workspace_turns', 'console_workspace_turns_action_check', 'action',
            'plan,email_batch']] LOOP
    SELECT pg_catalog.pg_get_constraintdef(c.oid) INTO def
      FROM pg_catalog.pg_constraint c
     WHERE c.conrelid = ('public.' || spec[1])::regclass AND c.conname = spec[2];
    IF def IS NULL THEN
      RAISE EXCEPTION '0542: % is missing on public.%', spec[2], spec[1];
    END IF;
    IF def LIKE '%''''%' OR def !~ ('^CHECK \(\(' || spec[3] || ' = ANY \(ARRAY\[') THEN
      RAISE EXCEPTION '0542: % does not read as a plain list of values: %', spec[2], def;
    END IF;
    SELECT array_agg(m[1] ORDER BY n) INTO allowed
      FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
    IF allowed IS NULL THEN
      RAISE EXCEPTION '0542: % lists no values: %', spec[2], def;
    END IF;
    wanted := string_to_array(spec[4], ',');
    IF NOT wanted <@ allowed THEN
      EXECUTE format('ALTER TABLE public.%I DROP CONSTRAINT %I', spec[1], spec[2]);
      EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (%I IN (%s))',
                     spec[1], spec[2], spec[3],
                     (SELECT string_agg(quote_literal(v), ', ' ORDER BY n)
                        FROM unnest(allowed || ARRAY(SELECT w FROM unnest(wanted) AS w
                                                      WHERE NOT w = ANY (allowed)))
                             WITH ORDINALITY AS u(v, n)));
    END IF;
  END LOOP;
END $$;

ALTER TABLE public.email_drafts ADD COLUMN IF NOT EXISTS facts_used jsonb;
