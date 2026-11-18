-- Customer Inbox refinement, part 2a: per-person console notifications and who gets them (design
--   docs/superpowers/specs/2026-11-16-inbox-refinement-design.md, sections 7 and 8; plan
--   docs/superpowers/plans/2026-11-18-inbox-refinement-2a-reminders-backend.md, task 3 and
--   decision 5).
-- What it adds:
--   console_notifications  one row per person and notification: kind (what sent it, such as
--              inbox.overdue), priority (low, normal, high, urgent), message_key and params (the
--              browser renders the text, so no finished text and no customer message is stored),
--              link (where it leads, ids only), dedupe_key, read_at (NULL while unread) and
--              created_at. (client_id, user_id, dedupe_key) is unique: a replay of the same
--              notification writes nothing (csa.notify.people.notify_people inserts ON CONFLICT DO
--              NOTHING). (user_id, client_id) references console_memberships as HR's
--              hr_notifications does: only a member of the company can hold a row (notify_people
--              skips a non-member before the insert; the key is the backstop). Indexes:
--              (client_id, user_id, created_at DESC) for a person's list and (client_id, user_id)
--              WHERE read_at IS NULL for the unread count.
--   public.inbox_team_user_ids(p_client): every account that may use the Inbox: an enabled
--              membership in p_client with role tenant_admin or tenant_staff (never read_only),
--              the account enabled and not a platform admin. Ordered by user id.
--   public.inbox_admin_user_ids(p_client): the same, tenant_admin only.
--   public.console_notification_members(p_client, p_users): those of p_users that may hold a
--              console notification in p_client: an enabled membership of any role (a read_only
--              member reads their own), the account enabled and not a platform admin. Distinct,
--              ordered by user id. notify_people filters its recipients through it, because the
--              app role cannot read console_memberships.
--   The three are SECURITY DEFINER (the worker and the gateway app role cannot read
--   console_users or console_memberships), search_path pinned, and refuse a p_client other than
--   app.client_id (inbox_recipients_unavailable, 42501), as 0635's hr_task_reminder_put does.
--   EXECUTE to csa_app only.
-- Row security: the usual tenant_isolation policy on console_notifications, ENABLE and no FORCE,
--   explicit grants (csa_ro reads; csa_app SELECT, INSERT, UPDATE and DELETE). Which person's rows
--   a route shows is the route's filter (plan task 7).
-- Pre-check: console_memberships has its (user_id, client_id) primary key (0270); the migration
--   stops with an error rather than guess if it does not. Nothing here touches hr_notifications or
--   another lane's table.
-- Safe to run twice: CREATE TABLE and CREATE INDEX IF NOT EXISTS, CREATE OR REPLACE FUNCTION, the
--   policy is dropped and made again, grants are revoked and granted again.
-- Rollback (as the database owner, after the application no longer calls them):
--   DROP FUNCTION public.console_notification_members(uuid,uuid[]);
--   DROP FUNCTION public.inbox_admin_user_ids(uuid);
--   DROP FUNCTION public.inbox_team_user_ids(uuid);
--   DROP TABLE public.console_notifications; then delete the schema_migrations row for 0624, or the
--   runner thinks it is still applied.

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_constraint c
     WHERE c.conrelid = 'public.console_memberships'::regclass AND c.contype = 'p'
       AND pg_catalog.pg_get_constraintdef(c.oid) = 'PRIMARY KEY (user_id, client_id)') THEN
    RAISE EXCEPTION '0624: console_memberships has no (user_id, client_id) primary key';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS public.console_notifications (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients (id),
    user_id uuid NOT NULL,
    kind text NOT NULL,
    priority text NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    message_key text NOT NULL,
    params jsonb NOT NULL DEFAULT '{}'::jsonb,
    link jsonb NOT NULL DEFAULT '{}'::jsonb,
    dedupe_key text NOT NULL,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, user_id, dedupe_key),
    FOREIGN KEY (user_id, client_id) REFERENCES public.console_memberships (user_id, client_id)
);
CREATE INDEX IF NOT EXISTS console_notifications_recent_idx
    ON public.console_notifications (client_id, user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS console_notifications_unread_idx
    ON public.console_notifications (client_id, user_id) WHERE read_at IS NULL;

-- Row security and grants, as in 0621. No FORCE: the owner csa keeps bypassing the policy.
ALTER TABLE public.console_notifications ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.console_notifications;
CREATE POLICY tenant_isolation ON public.console_notifications
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.console_notifications FROM PUBLIC, csa_app, csa_ro, csa_identity;
GRANT SELECT ON public.console_notifications TO csa_ro;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.console_notifications TO csa_app;

-- Who may use the Inbox (decision 5): admins and staff, never read_only, never a platform admin.
CREATE OR REPLACE FUNCTION public.inbox_team_user_ids(p_client uuid)
RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS NULL OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'inbox_recipients_unavailable' USING ERRCODE='42501'; END IF;
 RETURN QUERY
 SELECT m.user_id FROM public.console_memberships m
 JOIN public.console_users u ON u.id=m.user_id AND u.is_enabled AND NOT u.is_platform_admin
 WHERE m.client_id=p_client AND m.is_enabled AND m.role IN ('tenant_admin','tenant_staff')
 ORDER BY m.user_id;
END $$;

CREATE OR REPLACE FUNCTION public.inbox_admin_user_ids(p_client uuid)
RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS NULL OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'inbox_recipients_unavailable' USING ERRCODE='42501'; END IF;
 RETURN QUERY
 SELECT m.user_id FROM public.console_memberships m
 JOIN public.console_users u ON u.id=m.user_id AND u.is_enabled AND NOT u.is_platform_admin
 WHERE m.client_id=p_client AND m.is_enabled AND m.role='tenant_admin'
 ORDER BY m.user_id;
END $$;

-- Who of p_users may hold a console notification here: any enabled member (read_only included).
CREATE OR REPLACE FUNCTION public.console_notification_members(p_client uuid, p_users uuid[])
RETURNS SETOF uuid
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 IF p_client IS NULL OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid THEN
  RAISE EXCEPTION 'inbox_recipients_unavailable' USING ERRCODE='42501'; END IF;
 RETURN QUERY
 SELECT m.user_id FROM public.console_memberships m
 JOIN public.console_users u ON u.id=m.user_id AND u.is_enabled AND NOT u.is_platform_admin
 WHERE m.client_id=p_client AND m.is_enabled AND m.user_id=ANY(coalesce(p_users,'{}'::uuid[]))
 ORDER BY m.user_id;
END $$;

DO $$ DECLARE f regprocedure; BEGIN
 FOREACH f IN ARRAY ARRAY['public.inbox_team_user_ids(uuid)'::regprocedure,
  'public.inbox_admin_user_ids(uuid)'::regprocedure,
  'public.console_notification_members(uuid,uuid[])'::regprocedure] LOOP
  EXECUTE format('REVOKE ALL ON FUNCTION %s FROM PUBLIC,csa_ro,csa_identity,csa_hr_maintenance',f);
  EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO csa_app',f);
 END LOOP;
END $$;
