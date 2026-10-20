-- Retain terminal grant history; at most one active grant per credential.
ALTER TABLE public.ecommerce_grants DROP CONSTRAINT ecommerce_grants_client_id_record_id_key;
CREATE UNIQUE INDEX ecommerce_one_active_grant
    ON public.ecommerce_grants (client_id, record_id) WHERE state = 'active';

CREATE TABLE public.ecommerce_authorization_attempts (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    context jsonb NOT NULL,
    state_hash text NOT NULL UNIQUE CHECK (state_hash ~ '^[0-9a-f]{64}$'),
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'cancelled', 'consumed', 'completed')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    expires_at timestamptz NOT NULL DEFAULT clock_timestamp() + interval '10 minutes',
    consumed_at timestamptz,
    grant_id uuid,
    CHECK (expires_at > created_at),
    CHECK ((status IN ('pending', 'cancelled') AND consumed_at IS NULL AND grant_id IS NULL)
        OR (status = 'consumed' AND consumed_at IS NOT NULL AND grant_id IS NULL)
        OR (status = 'completed' AND consumed_at IS NOT NULL AND grant_id IS NOT NULL)),
    FOREIGN KEY (client_id, grant_id) REFERENCES public.ecommerce_grants(client_id, id)
);
CREATE FUNCTION public.guard_ecommerce_authorization_attempt() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - 'status' - 'consumed_at' - 'grant_id')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'consumed_at' - 'grant_id') THEN
        RAISE EXCEPTION 'authorization attempt binding is immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status = 'pending' AND NEW.status IN ('cancelled', 'consumed')
       AND OLD.expires_at > clock_timestamp() THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'consumed' AND NEW.status = 'completed'
       AND NEW.consumed_at = OLD.consumed_at AND OLD.expires_at > clock_timestamp() THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid authorization attempt transition' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER ecommerce_authorization_attempt_guard BEFORE UPDATE
    ON public.ecommerce_authorization_attempts FOR EACH ROW
    EXECUTE FUNCTION public.guard_ecommerce_authorization_attempt();
ALTER TABLE public.ecommerce_authorization_attempts ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.ecommerce_authorization_attempts
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.ecommerce_authorization_attempts FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT ON public.ecommerce_authorization_attempts TO csa_app;
GRANT UPDATE (status, consumed_at, grant_id) ON public.ecommerce_authorization_attempts TO csa_app;
GRANT SELECT ON public.ecommerce_authorization_attempts TO csa_ro;
