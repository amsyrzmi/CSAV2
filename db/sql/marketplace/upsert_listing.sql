-- One listing per SKU, idempotent on (client_id, channel, external_sku_id). Same newer-wins rule
-- as orders; no row back means stale. Never touches products, stock or reservations.
INSERT INTO public.marketplace_listings AS l (
    client_id, store_id, channel, external_product_id, external_sku_id, product_name,
    variant_name, seller_sku, price, currency, stock_qty, product_status, image_url,
    provider_updated_at, synced_at
) VALUES (
    %(client_id)s, %(store_id)s, %(channel)s, %(external_product_id)s, %(external_sku_id)s,
    %(product_name)s, %(variant_name)s, %(seller_sku)s, %(price)s, %(currency)s, %(stock_qty)s,
    %(product_status)s, %(image_url)s, %(provider_updated_at)s, %(synced_at)s
)
ON CONFLICT (client_id, channel, external_sku_id) DO UPDATE SET
    store_id = EXCLUDED.store_id,
    external_product_id = EXCLUDED.external_product_id,
    product_name = EXCLUDED.product_name,
    variant_name = EXCLUDED.variant_name,
    seller_sku = EXCLUDED.seller_sku,
    price = EXCLUDED.price,
    currency = EXCLUDED.currency,
    stock_qty = EXCLUDED.stock_qty,
    product_status = EXCLUDED.product_status,
    image_url = EXCLUDED.image_url,
    provider_updated_at = COALESCE(EXCLUDED.provider_updated_at, l.provider_updated_at),
    synced_at = EXCLUDED.synced_at
WHERE l.provider_updated_at IS NULL
   OR EXCLUDED.provider_updated_at IS NULL
   OR l.provider_updated_at <= EXCLUDED.provider_updated_at
RETURNING id;
