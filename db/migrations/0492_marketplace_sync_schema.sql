-- 0492_marketplace_sync_schema.sql — marketplace orders columns, product listings and sync jobs.
-- Preconditions: 0491 applied (channel_credentials is the store row); orders from the baseline.
-- Rollback: DROP TABLE public.marketplace_sync_jobs; DROP TABLE public.marketplace_listings;
--   DELETE FROM public.order_items WHERE order_id IN (SELECT id FROM public.orders WHERE
--   channel IN ('tiktok_shop','shopee') AND store_id IS NOT NULL);
--   DELETE FROM public.orders WHERE channel IN ('tiktok_shop','shopee') AND store_id IS NOT NULL;
--   DROP INDEX public.orders_marketplace_external_key;
--   ALTER TABLE public.orders DROP COLUMN buyer_cleared_at, DROP COLUMN provider_status,
--     DROP COLUMN provider_updated_at, DROP COLUMN synced_at, DROP COLUMN store_id;
-- RLS: both new tables get tenant_isolation as in 0280; orders already has it (0110). No FORCE.
-- Note: the new orders index duplicates the baseline orders_external_uq key (client_id, channel,
--   external_order_id, for every non-null external id). It is a unique INDEX, not a constraint, so
--   ON CONFLICT ON CONSTRAINT cannot name it. Upserts use the inference form:
--   ON CONFLICT (client_id, channel, external_order_id)
--     WHERE channel IN ('tiktok_shop','shopee') AND external_order_id IS NOT NULL DO UPDATE ...

ALTER TABLE public.orders
    -- The channel_credentials row the order was synced from (NULL for local orders).
    ADD COLUMN store_id uuid REFERENCES public.channel_credentials(id) ON DELETE SET NULL,
    ADD COLUMN synced_at timestamptz,
    ADD COLUMN provider_updated_at timestamptz,
    -- The marketplace's own status word, shown next to the local status.
    ADD COLUMN provider_status text,
    -- Set when buyer details (ship_to) were cleared by the retention job.
    ADD COLUMN buyer_cleared_at timestamptz;

CREATE UNIQUE INDEX orders_marketplace_external_key
    ON public.orders (client_id, channel, external_order_id)
    WHERE channel IN ('tiktok_shop', 'shopee') AND external_order_id IS NOT NULL;

CREATE TABLE public.marketplace_listings (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    store_id uuid NOT NULL REFERENCES public.channel_credentials(id) ON DELETE CASCADE,
    channel text NOT NULL CHECK (channel IN ('tiktok_shop', 'shopee')),
    external_product_id text NOT NULL,
    external_sku_id text NOT NULL,
    product_name text NOT NULL,
    variant_name text,
    seller_sku text,
    price numeric(12,2),
    currency text NOT NULL DEFAULT 'MYR',
    stock_qty integer CHECK (stock_qty IS NULL OR stock_qty >= 0),
    product_status text,
    image_url text,
    provider_updated_at timestamptz,
    synced_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (client_id, channel, external_sku_id)
);
CREATE INDEX marketplace_listings_store_idx ON public.marketplace_listings (client_id, store_id);
CREATE INDEX marketplace_listings_seller_sku_idx
    ON public.marketplace_listings (client_id, seller_sku) WHERE seller_sku IS NOT NULL;

CREATE TABLE public.marketplace_sync_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id uuid NOT NULL REFERENCES public.clients(id),
    store_id uuid NOT NULL REFERENCES public.channel_credentials(id) ON DELETE CASCADE,
    kind text NOT NULL CHECK (kind IN (
        'backfill_orders', 'backfill_products', 'order_refresh', 'product_refresh', 'catch_up')),
    -- An order or product id; NULL for whole-store jobs.
    target text,
    status text NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'claimed', 'done', 'failed', 'cancelled')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    last_error_code text,
    created_at timestamptz NOT NULL DEFAULT now()
);
-- Folds duplicate webhook triggers into one pending job (kb_ingest_jobs pattern).
CREATE UNIQUE INDEX marketplace_sync_jobs_pending_key
    ON public.marketplace_sync_jobs (client_id, store_id, kind, coalesce(target, ''))
    WHERE status = 'pending';
CREATE INDEX marketplace_sync_jobs_due_idx
    ON public.marketplace_sync_jobs (next_attempt_at) WHERE status = 'pending';

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['marketplace_listings','marketplace_sync_jobs'] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I USING '
            || '(client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid) '
            || 'WITH CHECK '
            || '(client_id = nullif(current_setting(''app.client_id'', true), '''')::uuid)', t);
        EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO csa_app', t);
        EXECUTE format('GRANT SELECT ON public.%I TO csa_ro', t);
    END LOOP;
END $$;
