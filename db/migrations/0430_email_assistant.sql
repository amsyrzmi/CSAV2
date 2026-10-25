-- Staff email assistant: per-user mailbox connections, cached thread metadata,
-- model analyses, local drafts and immutable receipts. Draft-only: nothing in
-- this schema or its services sends email. Mailbox tokens are encrypted at the
-- application layer and readable only by the connecting user's own session.
-- Precondition: 0410 and console identity (hr_lock_session, hr_immutable_record).
-- Rollback: disable the tenant switch and route; preserve receipts.
-- Additive only; no FORCE RLS and no grants to the identity role.

CREATE TABLE public.email_settings(
 client_id uuid PRIMARY KEY REFERENCES public.clients(id),
 enabled boolean NOT NULL DEFAULT false,
 changed_by uuid REFERENCES public.console_users(id),
 changed_at timestamptz NOT NULL DEFAULT clock_timestamp());
INSERT INTO public.email_settings(client_id) SELECT id FROM public.clients ON CONFLICT DO NOTHING;
CREATE FUNCTION public.email_new_client_settings() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
 INSERT INTO public.email_settings(client_id) VALUES(NEW.id) ON CONFLICT DO NOTHING;
 RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.email_new_client_settings() FROM PUBLIC,csa_app,csa_identity,csa_ro;
CREATE TRIGGER email_settings_for_new_client AFTER INSERT ON public.clients
 FOR EACH ROW EXECUTE FUNCTION public.email_new_client_settings();
ALTER TABLE public.email_settings ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.email_settings FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT ON public.email_settings TO csa_app;
CREATE POLICY email_settings_tenant ON public.email_settings FOR SELECT TO csa_app
 USING(client_id=nullif(current_setting('app.client_id',true),'')::uuid);

-- Only an enabled tenant administrator, verified through hr_lock_session, may
-- switch the assistant on or off for the tenant.
CREATE FUNCTION public.email_set_enabled(p_client uuid,p_enabled boolean) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE actor uuid;
BEGIN
 actor:=nullif(current_setting('app.hr_user_id',true),'')::uuid;
 IF actor IS NULL OR p_client IS DISTINCT FROM nullif(current_setting('app.client_id',true),'')::uuid
  THEN RETURN false; END IF;
 PERFORM 1 FROM public.console_memberships WHERE client_id=p_client AND user_id=actor
  AND is_enabled AND role='tenant_admin' FOR SHARE;
 IF NOT FOUND THEN RETURN false; END IF;
 UPDATE public.email_settings SET enabled=p_enabled,changed_by=actor,changed_at=clock_timestamp()
  WHERE client_id=p_client;
 RETURN FOUND;
END $$;
REVOKE ALL ON FUNCTION public.email_set_enabled(uuid,boolean) FROM PUBLIC,csa_identity,csa_ro;
GRANT EXECUTE ON FUNCTION public.email_set_enabled(uuid,boolean) TO csa_app;

CREATE TABLE public.email_mailboxes(
 client_id uuid NOT NULL REFERENCES public.clients(id),
 user_id uuid NOT NULL,
 id uuid PRIMARY KEY,
 provider text NOT NULL DEFAULT 'gmail' CHECK(provider='gmail'),
 address text CHECK(address IS NULL OR length(address) BETWEEN 3 AND 254),
 status text NOT NULL CHECK(status IN ('pending','connected','revoked')),
 access_token text,
 refresh_token text,
 token_expires_at timestamptz,
 scopes text[] NOT NULL DEFAULT '{}',
 history_id text,
 last_sync_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(client_id,user_id),
 FOREIGN KEY(user_id,client_id) REFERENCES public.console_memberships(user_id,client_id));
ALTER TABLE public.email_mailboxes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.email_mailboxes FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.email_mailboxes TO csa_app;
CREATE POLICY email_mailbox_self ON public.email_mailboxes TO csa_app
 USING(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid)
 WITH CHECK(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);

CREATE TABLE public.email_oauth_states(
 client_id uuid NOT NULL,
 user_id uuid NOT NULL,
 mailbox_id uuid NOT NULL REFERENCES public.email_mailboxes(id) ON DELETE CASCADE,
 state_hash text PRIMARY KEY CHECK(state_hash~'^[0-9a-f]{64}$'),
 code_verifier text NOT NULL CHECK(length(code_verifier) BETWEEN 43 AND 128),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 FOREIGN KEY(user_id,client_id) REFERENCES public.console_memberships(user_id,client_id));
ALTER TABLE public.email_oauth_states ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.email_oauth_states FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,INSERT,DELETE ON public.email_oauth_states TO csa_app;
CREATE POLICY email_oauth_state_self ON public.email_oauth_states TO csa_app
 USING(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid)
 WITH CHECK(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);

CREATE TABLE public.email_threads(
 client_id uuid NOT NULL,
 mailbox_id uuid NOT NULL REFERENCES public.email_mailboxes(id) ON DELETE CASCADE,
 thread_id text NOT NULL CHECK(length(thread_id) BETWEEN 1 AND 256),
 subject text NOT NULL DEFAULT '' CHECK(length(subject)<=500),
 sender text NOT NULL DEFAULT '' CHECK(length(sender)<=500),
 snippet text NOT NULL DEFAULT '' CHECK(length(snippet)<=1000),
 latest_message_id text,
 latest_body text CHECK(latest_body IS NULL OR length(latest_body)<=20000),
 received_at timestamptz,
 labels text[] NOT NULL DEFAULT '{}',
 unread boolean NOT NULL DEFAULT false,
 category text CHECK(category IN ('customer_request','order_or_delivery','complaint','billing',
  'vendor_or_partner','internal','newsletter_or_promotion','spam_or_phishing','other')),
 priority text CHECK(priority IN ('low','normal','high')),
 summary text CHECK(summary IS NULL OR length(summary)<=1200),
 action_items jsonb NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(action_items)='array'),
 analysed_at timestamptz,
 synced_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(client_id,mailbox_id,thread_id));
ALTER TABLE public.email_threads ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.email_threads FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.email_threads TO csa_app;
CREATE POLICY email_thread_owner ON public.email_threads TO csa_app
 USING(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND mailbox_id IN (SELECT m.id FROM public.email_mailboxes m
   WHERE m.client_id=nullif(current_setting('app.client_id',true),'')::uuid
   AND m.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid))
 WITH CHECK(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND mailbox_id IN (SELECT m.id FROM public.email_mailboxes m
   WHERE m.client_id=nullif(current_setting('app.client_id',true),'')::uuid
   AND m.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid));

CREATE TABLE public.email_drafts(
 client_id uuid NOT NULL,
 mailbox_id uuid NOT NULL REFERENCES public.email_mailboxes(id) ON DELETE CASCADE,
 id uuid PRIMARY KEY,
 thread_id text CHECK(thread_id IS NULL OR length(thread_id) BETWEEN 1 AND 256),
 recipient text NOT NULL CHECK(length(recipient) BETWEEN 3 AND 254),
 subject text NOT NULL CHECK(length(subject) BETWEEN 1 AND 200),
 body text NOT NULL CHECK(length(body) BETWEEN 1 AND 20000),
 status text NOT NULL CHECK(status IN ('draft','exported')),
 source text NOT NULL CHECK(source IN ('model','human')),
 gmail_draft_id text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
ALTER TABLE public.email_drafts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.email_drafts FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,INSERT,UPDATE,DELETE ON public.email_drafts TO csa_app;
CREATE POLICY email_draft_owner ON public.email_drafts TO csa_app
 USING(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND mailbox_id IN (SELECT m.id FROM public.email_mailboxes m
   WHERE m.client_id=nullif(current_setting('app.client_id',true),'')::uuid
   AND m.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid))
 WITH CHECK(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND mailbox_id IN (SELECT m.id FROM public.email_mailboxes m
   WHERE m.client_id=nullif(current_setting('app.client_id',true),'')::uuid
   AND m.user_id=nullif(current_setting('app.hr_user_id',true),'')::uuid));

CREATE TABLE public.email_receipts(
 client_id uuid NOT NULL,
 subject_id uuid NOT NULL,
 request_key uuid NOT NULL,
 operation text NOT NULL CHECK(length(operation) BETWEEN 1 AND 64),
 fingerprint text NOT NULL CHECK(fingerprint~'^[0-9a-f]{64}$'),
 receipt jsonb NOT NULL CHECK(jsonb_typeof(receipt)='object'),
 occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(client_id,subject_id,request_key),
 FOREIGN KEY(subject_id,client_id) REFERENCES public.console_memberships(user_id,client_id));
ALTER TABLE public.email_receipts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.email_receipts FROM PUBLIC,csa_app,csa_ro,csa_identity;
GRANT SELECT,INSERT ON public.email_receipts TO csa_app;
CREATE POLICY email_receipt_self ON public.email_receipts TO csa_app
 USING(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid)
 WITH CHECK(client_id=nullif(current_setting('app.client_id',true),'')::uuid
  AND subject_id=nullif(current_setting('app.hr_user_id',true),'')::uuid);
CREATE TRIGGER email_receipt_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.email_receipts
 FOR EACH STATEMENT EXECUTE FUNCTION public.hr_immutable_record();
