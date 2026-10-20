-- Internal verified-grant metadata only. No credential backfill or provider activation.
CREATE TABLE public.ecommerce_grants (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    record_id uuid NOT NULL,
    platform text NOT NULL CHECK (platform = 'tiktok_shop'),
    shop_id text NOT NULL CHECK (length(shop_id) BETWEEN 1 AND 100),
    app_id text NOT NULL CHECK (length(app_id) BETWEEN 1 AND 100),
    credential_generation bigint NOT NULL CHECK (credential_generation >= 1),
    revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
    state text NOT NULL CHECK (state IN ('active', 'revoked', 'disconnected')),
    source text NOT NULL CHECK (source = 'verified_authorization_exchange'),
    scopes text[] NOT NULL,
    verified_at timestamptz NOT NULL,
    expires_at timestamptz NOT NULL CHECK (expires_at > verified_at),
    UNIQUE (client_id, record_id),
    UNIQUE (client_id, id)
);
-- No credential FK: deleting a credential must not delete lifecycle evidence.
CREATE TABLE public.ecommerce_grant_events (
    client_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 1),
    state text NOT NULL CHECK (state IN ('active', 'revoked', 'disconnected')),
    occurred_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (client_id, grant_id, revision),
    FOREIGN KEY (client_id, grant_id) REFERENCES public.ecommerce_grants(client_id, id)
);

CREATE FUNCTION public.guard_ecommerce_grant_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.state <> 'active' OR NEW.state NOT IN ('revoked', 'disconnected')
       OR NEW.revision <> OLD.revision + 1
       OR (to_jsonb(NEW) - 'state' - 'revision')
          IS DISTINCT FROM (to_jsonb(OLD) - 'state' - 'revision') THEN
        RAISE EXCEPTION 'invalid ecommerce grant transition' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ecommerce_grant_update_guard BEFORE UPDATE ON public.ecommerce_grants
    FOR EACH ROW EXECUTE FUNCTION public.guard_ecommerce_grant_update();

CREATE FUNCTION public.audit_ecommerce_grant() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    INSERT INTO public.ecommerce_grant_events (client_id, grant_id, revision, state)
    VALUES (NEW.client_id, NEW.id, NEW.revision, NEW.state);
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.audit_ecommerce_grant() FROM PUBLIC, csa_app, csa_ro;
CREATE TRIGGER ecommerce_grant_audit AFTER INSERT OR UPDATE ON public.ecommerce_grants
    FOR EACH ROW EXECUTE FUNCTION public.audit_ecommerce_grant();

ALTER TABLE public.ecommerce_grants ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.ecommerce_grants
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
ALTER TABLE public.ecommerce_grant_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.ecommerce_grant_events
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.ecommerce_grants, public.ecommerce_grant_events
    FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT ON public.ecommerce_grants TO csa_app;
GRANT UPDATE (state, revision) ON public.ecommerce_grants TO csa_app;
GRANT SELECT ON public.ecommerce_grant_events TO csa_app;
GRANT SELECT ON public.ecommerce_grants, public.ecommerce_grant_events TO csa_ro;
