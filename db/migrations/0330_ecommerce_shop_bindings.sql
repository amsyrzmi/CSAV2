-- Empty trusted exchange evidence; no legacy metadata/ciphertext backfill.
CREATE TABLE public.ecommerce_shop_bindings (
    client_id uuid NOT NULL,
    grant_id uuid PRIMARY KEY,
    shop_cipher text NOT NULL CHECK (shop_cipher LIKE 'v1:%' AND length(shop_cipher) > 3),
    FOREIGN KEY (client_id, grant_id) REFERENCES public.ecommerce_grants(client_id, id)
);

CREATE FUNCTION public.guard_ecommerce_shop_binding() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'shop binding evidence is immutable' USING ERRCODE = '23514';
END $$;
CREATE TRIGGER ecommerce_shop_binding_guard BEFORE UPDATE OR DELETE
    ON public.ecommerce_shop_bindings FOR EACH ROW
    EXECUTE FUNCTION public.guard_ecommerce_shop_binding();

ALTER TABLE public.ecommerce_shop_bindings ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.ecommerce_shop_bindings
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.ecommerce_shop_bindings FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT, INSERT ON public.ecommerce_shop_bindings TO csa_app;
GRANT SELECT ON public.ecommerce_shop_bindings TO csa_ro;
