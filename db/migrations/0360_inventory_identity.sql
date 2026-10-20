-- Identity scaffolding only: no enrollment, quantities, backfill or legacy writer changes.
ALTER TABLE public.products
    ADD CONSTRAINT products_client_id_id_key UNIQUE (client_id, id);
ALTER TABLE public.orders
    ADD CONSTRAINT orders_client_id_id_key UNIQUE (client_id, id);
ALTER TABLE public.order_items
    ADD CONSTRAINT order_items_client_id_id_product_id_key
        UNIQUE (client_id, id, product_id);

CREATE TABLE public.inventory_warehouses (
    id uuid PRIMARY KEY,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    code text NOT NULL CHECK (code ~ '[^[:space:]]'),
    is_default boolean NOT NULL,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (client_id, id),
    UNIQUE (client_id, code)
);
CREATE UNIQUE INDEX inventory_one_default_warehouse
    ON public.inventory_warehouses (client_id) WHERE is_default;

ALTER TABLE public.inventory_warehouses ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.inventory_warehouses
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
REVOKE ALL ON public.inventory_warehouses FROM PUBLIC, csa_app, csa_ro;
GRANT SELECT ON public.inventory_warehouses TO csa_app, csa_ro;
