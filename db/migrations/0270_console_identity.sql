-- CSAV2-owned identity. No imported accounts, owner gateway or FORCE RLS.
-- Rollback: disable session endpoints and revoke csa_identity LOGIN; preserve audit.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'csa_identity') THEN
        CREATE ROLE csa_identity NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOREPLICATION NOBYPASSRLS;
    END IF;
END $$;
GRANT USAGE ON SCHEMA public TO csa_identity;

CREATE TABLE public.console_users (
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    email text NOT NULL UNIQUE CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
    display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 120),
    password_hash text,
    is_platform_admin boolean NOT NULL DEFAULT false,
    is_enabled boolean NOT NULL DEFAULT true,
    must_change_password boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.console_memberships (
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    role text NOT NULL CHECK (role IN ('tenant_admin', 'tenant_staff', 'read_only')),
    is_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, client_id)
);
CREATE TABLE public.console_sessions (
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    token_hash text NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    csrf_hash text NOT NULL CHECK (length(csrf_hash) = 64),
    client_id uuid REFERENCES public.clients(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > created_at)
);
CREATE INDEX console_sessions_user ON public.console_sessions(user_id);
CREATE TABLE public.console_tokens (
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    user_id uuid NOT NULL REFERENCES public.console_users(id),
    client_id uuid REFERENCES public.clients(id),
    role text CHECK (role IN ('tenant_admin', 'tenant_staff', 'read_only')),
    kind text NOT NULL CHECK (kind IN ('invite', 'reset')),
    token_hash text NOT NULL UNIQUE CHECK (length(token_hash) = 64),
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    revoked_at timestamptz,
    created_by uuid NOT NULL REFERENCES public.console_users(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((kind = 'invite' AND client_id IS NOT NULL AND role IS NOT NULL)
        OR (kind = 'reset' AND client_id IS NULL AND role IS NULL))
);
CREATE INDEX console_tokens_user ON public.console_tokens(user_id);
CREATE TABLE public.console_login_attempts (
    bucket text PRIMARY KEY CHECK (length(bucket) = 64),
    attempts integer NOT NULL CHECK (attempts >= 0),
    window_started_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX console_login_attempts_window ON public.console_login_attempts(window_started_at);
CREATE TABLE public.console_audit (
    id uuid PRIMARY KEY DEFAULT public.uuid_generate_v4(),
    actor_id uuid REFERENCES public.console_users(id),
    client_id uuid REFERENCES public.clients(id),
    action text NOT NULL CHECK (length(action) BETWEEN 1 AND 100),
    target_id text NOT NULL CHECK (length(target_id) BETWEEN 1 AND 128),
    details jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX console_audit_tenant_time ON public.console_audit(client_id, created_at DESC);
ALTER TABLE public.console_memberships ENABLE ROW LEVEL SECURITY;
CREATE POLICY membership_read ON public.console_memberships FOR SELECT USING (
    client_id = nullif(current_setting('app.client_id', true), '')::uuid
    OR user_id = nullif(current_setting('app.console_user_id', true), '')::uuid
);
CREATE POLICY membership_write ON public.console_memberships FOR ALL USING (
    client_id = nullif(current_setting('app.client_id', true), '')::uuid
) WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
ALTER TABLE public.console_audit ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.console_audit USING (
    client_id = nullif(current_setting('app.client_id', true), '')::uuid
    OR (current_user = 'csa_identity' AND client_id IS NULL)
) WITH CHECK (
    client_id = nullif(current_setting('app.client_id', true), '')::uuid
    OR (current_user = 'csa_identity' AND client_id IS NULL)
);
-- Owner default grants from 0100 must not expose password/session/token material.
REVOKE ALL ON public.console_users, public.console_memberships, public.console_sessions,
    public.console_tokens, public.console_login_attempts, public.console_audit
    FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT, UPDATE ON public.console_users, public.console_memberships,
    public.console_sessions, public.console_tokens, public.console_login_attempts TO csa_identity;
GRANT DELETE ON public.console_login_attempts TO csa_identity;
GRANT SELECT, INSERT ON public.console_audit TO csa_identity;
GRANT INSERT ON public.console_audit TO csa_app;
-- Session/token lookup precedes tenant resolution; only the isolated identity role
-- may perform these global lookups. Domain roles retain no grants.
ALTER TABLE public.console_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY identity_lookup ON public.console_sessions TO csa_identity
    USING (true) WITH CHECK (true);
ALTER TABLE public.console_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY identity_lookup ON public.console_tokens TO csa_identity
    USING (true) WITH CHECK (true);
-- The identity role inherits no unrelated role privileges.
ALTER ROLE csa_identity NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
-- This known trigger definer is the only baseline definer still exposed through
-- PUBLIC. 0100 explicitly grants csa_app EXECUTE and owner rights are unchanged;
-- resolve_endpoint already revoked PUBLIC in 0120. No other functions are changed.
REVOKE EXECUTE ON FUNCTION public.enqueue_webhook_delivery() FROM PUBLIC;
