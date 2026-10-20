-- Version every credential mutation without importing or exposing credential material.
ALTER TABLE public.channel_credentials
    ADD COLUMN credential_generation bigint NOT NULL DEFAULT 1
    CHECK (credential_generation >= 1);

CREATE FUNCTION public.fence_ecommerce_credentials() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF (to_jsonb(NEW) - 'updated_at' - 'credential_generation')
           IS NOT DISTINCT FROM (to_jsonb(OLD) - 'updated_at' - 'credential_generation') THEN
            IF NEW.credential_generation <> OLD.credential_generation THEN
                RAISE EXCEPTION 'credential generation is database managed' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        NEW.credential_generation := OLD.credential_generation + 1;
    END IF;
    UPDATE public.ecommerce_grants
    SET state = 'disconnected', revision = revision + 1
    WHERE client_id = OLD.client_id AND record_id = OLD.id AND state = 'active';
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.fence_ecommerce_credentials() FROM PUBLIC, csa_app, csa_ro;
CREATE TRIGGER ecommerce_credential_fence BEFORE UPDATE OR DELETE
    ON public.channel_credentials FOR EACH ROW
    EXECUTE FUNCTION public.fence_ecommerce_credentials();

CREATE TABLE public.ecommerce_read_operations (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL,
    grant_id uuid NOT NULL,
    operation jsonb NOT NULL,
    request jsonb NOT NULL,
    consumed_at timestamptz,
    FOREIGN KEY (client_id, grant_id) REFERENCES public.ecommerce_grants(client_id, id)
);
CREATE FUNCTION public.guard_ecommerce_read_operation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.consumed_at IS NOT NULL OR NEW.consumed_at IS NULL
       OR (to_jsonb(NEW) - 'consumed_at') IS DISTINCT FROM (to_jsonb(OLD) - 'consumed_at') THEN
        RAISE EXCEPTION 'invalid ecommerce read operation transition' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ecommerce_read_operation_guard BEFORE UPDATE
    ON public.ecommerce_read_operations FOR EACH ROW
    EXECUTE FUNCTION public.guard_ecommerce_read_operation();
ALTER TABLE public.ecommerce_read_operations ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.ecommerce_read_operations
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.ecommerce_read_operations FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT ON public.ecommerce_read_operations TO csa_app;
GRANT UPDATE (consumed_at) ON public.ecommerce_read_operations TO csa_app;
GRANT SELECT ON public.ecommerce_read_operations TO csa_ro;
