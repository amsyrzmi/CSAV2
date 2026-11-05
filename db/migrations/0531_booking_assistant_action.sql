-- The staff assistant's booking card: console_proposals_kind_check also allows 'booking_action' and
--   console_workspace_turns_action_check also allows 'booking_action'. console_workspace_turns_page_key_check
--   (the tab a turn was asked on, 0460) also allows 'bookings' and 'subscriptions': it never listed
--   either tab, so a turn asked on one of them could not be stored. Every kind, action and page
--   already allowed is kept.
-- Why this is 0531 and not part of 0512: 0521 drops and re-adds both constraints with fixed lists,
--   and on a fresh database 0512 runs before 0521, so a constraint built in 0512 would be undone.
--   0531 runs after 0521 on every database (fresh, or already past 0530 like staging).
-- How: the allowed values are read from each constraint as the database stores it
--   (pg_get_constraintdef), 'booking_action' is added when it is missing, and the constraint is
--   built again from that list. No kind or action of another track is named here, so one this
--   migration has never heard of is not lost. When every value this file adds is already there the
--   constraint is left alone, so running the file again changes nothing.
-- No tables, columns, policies, grants or roles change. FORCE RLS stays off.
-- Pre-check: the three constraints exist and are plain lists of quoted values (0460, 0484, 0521); the
--   migration stops with an error rather than guess if one is missing or reads differently.
-- Rollback: DELETE FROM public.console_proposals WHERE kind = 'booking_action'; DELETE FROM
--   public.console_workspace_turns WHERE action = 'booking_action' OR page_key IN ('bookings',
--   'subscriptions'); then drop the three constraints and add them again with the lists from 0521
--   (kind, action) and 0460 (page_key) plus whatever later migrations added. Finally delete the
--   schema_migrations row for 0531, or the runner thinks it is still applied.

DO $$
DECLARE
  spec text[];
  def text;
  allowed text[];
  wanted text[];
BEGIN
  -- Each spec: table, constraint, column, and the values this file adds (comma separated).
  FOREACH spec SLICE 1 IN ARRAY ARRAY[
      ARRAY['console_proposals', 'console_proposals_kind_check', 'kind', 'booking_action'],
      ARRAY['console_workspace_turns', 'console_workspace_turns_action_check', 'action',
            'booking_action'],
      ARRAY['console_workspace_turns', 'console_workspace_turns_page_key_check', 'page_key',
            'bookings,subscriptions']] LOOP
    SELECT pg_catalog.pg_get_constraintdef(c.oid) INTO def
      FROM pg_catalog.pg_constraint c
     WHERE c.conrelid = ('public.' || spec[1])::regclass AND c.conname = spec[2];
    IF def IS NULL THEN
      RAISE EXCEPTION '0531: % is missing on public.%', spec[2], spec[1];
    END IF;
    IF def LIKE '%''''%' OR def !~ ('^CHECK \(\(' || spec[3] || ' = ANY \(ARRAY\[') THEN
      RAISE EXCEPTION '0531: % does not read as a plain list of values: %', spec[2], def;
    END IF;
    SELECT array_agg(m[1] ORDER BY n) INTO allowed
      FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
    IF allowed IS NULL THEN
      RAISE EXCEPTION '0531: % lists no values: %', spec[2], def;
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
