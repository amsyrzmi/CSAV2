-- Customer sign-up from the chat: the request a customer files, the `pending` subscription status,
--   two plan switches and a per-company expiry setting.
-- Adds:
--   public.signup_requests: one row per sign-up a customer filed in the chat. client_id references
--     clients and contact_id references contacts; a trigger (the same function as
--     trg_preparation_contact_tenant, 0260) refuses a contact of another company. status is
--     pending, approved, declined or expired; decided_via is staff or auto. phone is the number as
--     the customer typed it, normalised; it is NOT verified unless the contact's phone_verified_at
--     (0581) says so. subscription_id is set when an approval creates a subscription (no foreign
--     key: the request is a record of what was asked). A unique partial index allows one pending
--     request per (company, contact, plan); a second index serves the staff list.
--   public.subscription_settings: one row per company at most, pending_expiry_days 0..90 (0 means a
--     pending subscription never expires; no row means the default of 14, applied by the app),
--     with a version for optimistic updates.
--   plans.signup_mode ('staff' or 'auto', default 'staff') and plans.is_signup_open (default true).
--   subscriptions.activated_at: when staff started a pending subscription (NULL until then).
--   subscriptions_status_chk also allows 'pending'. The allowed values are read from the live
--     constraint and 'pending' is added, as 0531 does, so a value another track added is kept.
--     When 'pending' is already there the constraint is left alone.
-- Row security on both new tables: the same tenant_isolation policy as training_items (0541), ENABLE
--   only, never FORCE. SELECT, INSERT, UPDATE, DELETE to csa_app; nothing to PUBLIC, csa_ro or
--   csa_identity. No new roles. Safe to run twice: IF NOT EXISTS, policies and triggers are dropped
--   and made again, the status check is left alone once it lists 'pending'.
-- Pre-check: 0560 applied; plans, subscriptions, contacts, clients and console_users exist;
--   subscriptions_status_chk (0480) exists and reads as a plain list of quoted values (the migration
--   stops with an error rather than guess). Adding the columns is cheap (constant defaults); the
--   status check is re-added with a full-table check of subscriptions, a small table.
-- Rollback (as the database owner, in this order, with the services stopped; the older image's
--   readiness check wants the highest applied version to equal its own):
--   UPDATE public.subscriptions SET status = 'cancelled', cancelled_at = now()
--   WHERE status = 'pending' (not a DELETE: each has a signup_approved event and
--   subscription_events_subscription_id_fkey does not cascade), then drop subscriptions_status_chk and add it again as CHECK (status IN ('active', 'paused',
--   'cancelled')) (0480), or any list a later migration set minus 'pending';
--   ALTER TABLE public.subscriptions DROP COLUMN activated_at;
--   ALTER TABLE public.plans DROP COLUMN is_signup_open, DROP COLUMN signup_mode;
--   DROP TABLE public.subscription_settings; DROP TABLE public.signup_requests;
--   (every sign-up request and expiry setting is lost). Finally delete the schema_migrations row for
--   0580, or the runner thinks it is still applied.

CREATE TABLE IF NOT EXISTS public.signup_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    contact_id uuid NOT NULL REFERENCES public.contacts(id),
    plan_code text NOT NULL,
    full_name text NOT NULL CHECK (char_length(full_name) BETWEEN 1 AND 120),
    phone text NOT NULL,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'declined', 'expired')),
    decided_via text CHECK (decided_via IN ('staff', 'auto')),
    decided_by uuid REFERENCES public.console_users(id),
    decline_reason text CHECK (char_length(decline_reason) <= 300),
    subscription_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    decided_at timestamptz
);

CREATE UNIQUE INDEX IF NOT EXISTS signup_requests_one_pending
    ON public.signup_requests (client_id, contact_id, plan_code) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS signup_requests_client_status
    ON public.signup_requests (client_id, status, created_at);

ALTER TABLE public.signup_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.signup_requests;
CREATE POLICY tenant_isolation ON public.signup_requests
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.signup_requests FROM PUBLIC, csa_ro, csa_identity;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.signup_requests TO csa_app;

DROP TRIGGER IF EXISTS trg_signup_contact_tenant ON public.signup_requests;
CREATE TRIGGER trg_signup_contact_tenant
    BEFORE INSERT OR UPDATE OF client_id, contact_id ON public.signup_requests
    FOR EACH ROW EXECUTE FUNCTION public.contact_identity_tenant_check();

CREATE TABLE IF NOT EXISTS public.subscription_settings (
    client_id uuid PRIMARY KEY REFERENCES public.clients(id),
    pending_expiry_days integer NOT NULL DEFAULT 14
        CHECK (pending_expiry_days BETWEEN 0 AND 90),
    version integer NOT NULL DEFAULT 1
);

ALTER TABLE public.subscription_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON public.subscription_settings;
CREATE POLICY tenant_isolation ON public.subscription_settings
    USING (client_id = nullif(current_setting('app.client_id',true),'')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id',true),'')::uuid);
REVOKE ALL ON public.subscription_settings FROM PUBLIC, csa_ro, csa_identity;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.subscription_settings TO csa_app;

ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS signup_mode text NOT NULL DEFAULT 'staff'
        CONSTRAINT plans_signup_mode_chk CHECK (signup_mode IN ('staff', 'auto')),
    ADD COLUMN IF NOT EXISTS is_signup_open boolean NOT NULL DEFAULT true;

ALTER TABLE public.subscriptions ADD COLUMN IF NOT EXISTS activated_at timestamptz;

-- subscriptions_status_chk also allows 'pending': read the live list, add what is missing.
DO $$
DECLARE
  spec text[];
  def text;
  allowed text[];
  wanted text[];
BEGIN
  -- Each spec: table, constraint, column, and the values this file adds (comma separated).
  FOREACH spec SLICE 1 IN ARRAY ARRAY[
      ARRAY['subscriptions', 'subscriptions_status_chk', 'status', 'pending']] LOOP
    SELECT pg_catalog.pg_get_constraintdef(c.oid) INTO def
      FROM pg_catalog.pg_constraint c
     WHERE c.conrelid = ('public.' || spec[1])::regclass AND c.conname = spec[2];
    IF def IS NULL THEN
      RAISE EXCEPTION '0580: % is missing on public.%', spec[2], spec[1];
    END IF;
    IF def LIKE '%''''%' OR def !~ ('^CHECK \(\(' || spec[3] || ' = ANY \(ARRAY\[') THEN
      RAISE EXCEPTION '0580: % does not read as a plain list of values: %', spec[2], def;
    END IF;
    SELECT array_agg(m[1] ORDER BY n) INTO allowed
      FROM regexp_matches(def, '''([^'']+)''', 'g') WITH ORDINALITY AS t(m, n);
    IF allowed IS NULL THEN
      RAISE EXCEPTION '0580: % lists no values: %', spec[2], def;
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
