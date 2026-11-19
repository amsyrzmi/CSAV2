-- Customer Inbox refinement, part 3: notification settings and email delivery (design
--   docs/superpowers/specs/2026-11-19-inbox-pr3-delivery-design.md, section 5.4; plan
--   docs/superpowers/plans/2026-11-19-inbox-refinement-3-delivery-settings.md, task 1).
-- What it adds:
--   console_notification_prefs  one row per person and company, written only when the person saves
--              (no row means email off): email_enabled (default false), kinds (the six inbox kinds,
--              default all six, every element checked), min_priority (low, normal, high, urgent,
--              default high), failure_notified_at (set once per failure episode so a dead email
--              raises one bell, not one per row) and updated_at. Primary key (client_id, user_id);
--              (user_id, client_id) references console_memberships as 0624 does.
--   console_email_outbox  the person email queue the worker drains: one row per notification
--              (notification_id is unique and a plain uuid, not a foreign key, so the 30-day
--              notification purge is unaffected), kind, priority, message_key, params and link (ids
--              and keys only, never a customer message), status (pending, delivering, delivered,
--              dead), attempts, next_attempt_at, locked_at, last_error, delivered_at, created_at.
--              Index (status, next_attempt_at) serves the due scan. user_id deliberately has no
--              membership foreign key: the sender treats "no address returned for this user" as
--              terminal (a person removed from the company is not retried).
--   staff_notification_targets.failure_notified_at  timestamptz, NULL: the once-per-episode flag of a
--              dead target.
--   staff_notification_targets.version  int NOT NULL DEFAULT 0: the optimistic-lock counter the API
--              bumps on each edit.
--   staff_notifications.held_at  timestamptz, NULL: set when quiet hours defer a row.
--   staff_notifications.status  also allows 'cancelled'. The constraint is read as the database
--              stores it (pg_get_constraintdef, the 0531 method) and rebuilt with every value it
--              lists plus 'cancelled'; one this file has never heard of is not lost.
--   public.console_member_emails(p_client_id, p_user_ids)  RETURNS TABLE (user_id, email): the
--              sign-in email of each enabled member of p_client_id among p_user_ids (account enabled,
--              not a platform admin; any role, a read_only member reads their own). SECURITY
--              DEFINER because the worker and the gateway app role cannot read console_users or
--              console_memberships; search_path pinned; EXECUTE to csa_app only. Like the 0624
--              functions it refuses a p_client_id that is NULL or other than app.client_id
--              (inbox_recipients_unavailable, 42501), so a session can only ask about the company
--              it is working in, and the membership join is on p_client_id, so another company's
--              person is never returned.
-- Row security: the usual tenant_isolation policy on both new tables, ENABLE and no FORCE, explicit
--   grants (csa_ro reads; csa_app SELECT, INSERT, UPDATE and DELETE). The three added columns need
--   neither.
-- Pre-check: console_memberships has its (user_id, client_id) primary key (0270) and the
--   staff_notifications status constraint reads as a plain list of quoted values (0160); the
--   migration stops with an error rather than guess if either differs.
-- Safe to run twice: CREATE TABLE, CREATE INDEX and ADD COLUMN IF NOT EXISTS, constraints guarded
--   by name, the status constraint is left alone when it already lists 'cancelled', the policy is
--   dropped and made again, CREATE OR REPLACE FUNCTION, grants are revoked and granted again.
-- Rollback (as the database owner, after the application no longer uses them):
--   DROP FUNCTION public.console_member_emails(uuid,uuid[]);
--   DROP TABLE public.console_email_outbox; DROP TABLE public.console_notification_prefs;
--   ALTER TABLE public.staff_notifications DROP COLUMN held_at;
--   ALTER TABLE public.staff_notification_targets DROP COLUMN version, DROP COLUMN failure_notified_at;
--   UPDATE public.staff_notifications SET status = 'dead' WHERE status = 'cancelled'; then drop the
--   status constraint and add it again without 'cancelled'; finally delete the schema_migrations
--   row for 0626, or the runner thinks it is still applied.

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid = 'public.console_memberships'::regclass AND c.contype = 'p'
       AND pg_catalog.pg_get_constraintdef(c.oid) = 'PRIMARY KEY (user_id, client_id)') THEN
    RAISE EXCEPTION '0626: console_memberships has no (user_id, client_id) primary key';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.console_notification_prefs (
    client_id uuid NOT NULL REFERENCES public.clients (id),
    user_id uuid NOT NULL,
    email_enabled boolean NOT NULL DEFAULT false,
    kinds text[] NOT NULL DEFAULT '{inbox.assigned,inbox.overdue,inbox.snooze_due,inbox.stale_claim,inbox.follow_up,inbox.handback}',
    min_priority text NOT NULL DEFAULT 'high'
        CONSTRAINT console_notification_prefs_min_priority_check
        CHECK (min_priority IN ('low', 'normal', 'high', 'urgent')),
    failure_notified_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (client_id, user_id),
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships (user_id, client_id),
    CONSTRAINT console_notification_prefs_kinds_check
        CHECK (kinds <@ ARRAY['inbox.assigned', 'inbox.overdue', 'inbox.snooze_due',
                              'inbox.stale_claim', 'inbox.follow_up', 'inbox.handback']::text[])
);

CREATE TABLE IF NOT EXISTS public.console_email_outbox (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients (id),
    user_id uuid NOT NULL,
    notification_id uuid NOT NULL UNIQUE,
    kind text NOT NULL,
    priority text NOT NULL,
    message_key text NOT NULL,
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    link jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'pending'
        CONSTRAINT console_email_outbox_status_check
        CHECK (status IN ('pending', 'delivering', 'delivered', 'dead')),
    attempts int NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    locked_at timestamptz,
    last_error text,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS console_email_outbox_due_idx
    ON public.console_email_outbox (status, next_attempt_at);

-- Row security and grants, as in 0624. No FORCE: the owner csa keeps bypassing the policy.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['console_notification_prefs', 'console_email_outbox'] LOOP
    EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON public.%I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON public.%I '
      'USING (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
      'WITH CHECK (client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
    EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, csa_app, csa_ro, csa_identity', t);
    EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO csa_app', t);
  END LOOP;
END $$;

-- The dead-letter flag, the edit counter and the quiet-hours hold. A column needs no policy or grant.
ALTER TABLE public.staff_notification_targets
    ADD COLUMN IF NOT EXISTS failure_notified_at timestamptz,
    ADD COLUMN IF NOT EXISTS version int NOT NULL DEFAULT 0;
ALTER TABLE public.staff_notifications
    ADD COLUMN IF NOT EXISTS held_at timestamptz;

-- staff_notifications.status also allows 'cancelled': read the live list, add the value, rebuild.
DO $$
DECLARE
  cname text;
  def text;
  allowed text[];
BEGIN
  SELECT c.conname, pg_catalog.pg_get_constraintdef(c.oid) INTO cname, def
    FROM pg_catalog.pg_constraint c
   WHERE c.conrelid = 'public.staff_notifications'::regclass AND c.contype = 'c'
     AND pg_catalog.pg_get_constraintdef(c.oid) ~ '^CHECK \(\(status = ANY \(ARRAY\[';
  IF def IS NULL THEN
    RAISE EXCEPTION '0626: the status check on public.staff_notifications is missing or reads differently';
  END IF;
  IF def LIKE '%''''%' THEN
    RAISE EXCEPTION '0626: % does not read as a plain list of values: %', cname, def;
  END IF;
  SELECT array_agg(m[1] ORDER BY n) INTO allowed
    FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
  IF allowed IS NULL THEN
    RAISE EXCEPTION '0626: % lists no values: %', cname, def;
  END IF;
  IF NOT 'cancelled' = ANY (allowed) THEN
    EXECUTE format('ALTER TABLE public.staff_notifications DROP CONSTRAINT %I', cname);
    EXECUTE format('ALTER TABLE public.staff_notifications ADD CONSTRAINT %I CHECK (status IN (%s))',
                   cname,
                   (SELECT string_agg(quote_literal(v), ', ' ORDER BY n)
                      FROM unnest(allowed || ARRAY['cancelled']) WITH ORDINALITY AS u(v, n)));
  END IF;
END $$;

-- The sign-in email of the enabled members of the transaction's company among the people asked for.
CREATE OR REPLACE FUNCTION public.console_member_emails(p_client_id uuid, p_user_ids uuid[])
RETURNS TABLE (user_id uuid, email text)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client_id IS NULL
    OR p_client_id IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'inbox_recipients_unavailable' USING ERRCODE='42501'; END IF;
 RETURN QUERY
 SELECT u.id, u.email
   FROM public.console_memberships m
   JOIN public.console_users u ON u.id = m.user_id AND u.is_enabled AND NOT u.is_platform_admin
  WHERE m.client_id = p_client_id AND m.is_enabled
    AND m.user_id = ANY (coalesce(p_user_ids, '{}'::uuid[]))
  ORDER BY u.id;
END $$;

REVOKE ALL ON FUNCTION public.console_member_emails(uuid, uuid[])
    FROM PUBLIC, csa_ro, csa_identity, csa_hr_maintenance;
GRANT EXECUTE ON FUNCTION public.console_member_emails(uuid, uuid[]) TO csa_app;
