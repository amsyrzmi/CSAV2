-- TikTok listings of one tenant, ordered by name then id (a stable keyset).
-- same_sku_product is looked up here, at read time, from the tenant's own products on an equal
-- SKU; it is never stored. A NULL or empty seller_sku matches nothing.
SELECT l.id, l.product_name, l.variant_name, l.seller_sku, l.price, l.currency, l.stock_qty,
       l.product_status, l.image_url, l.synced_at, m.name AS same_sku_product
FROM public.marketplace_listings l
LEFT JOIN LATERAL (
    SELECT p.name FROM public.products p
    WHERE p.client_id = l.client_id AND p.sku = l.seller_sku AND l.seller_sku <> ''
    ORDER BY p.created_at, p.id LIMIT 1
) m ON true
WHERE l.client_id = %(client_id)s AND l.channel = 'tiktok_shop'
  AND (%(store_id)s::uuid IS NULL OR l.store_id = %(store_id)s::uuid)
  AND (%(pattern)s::text IS NULL
       OR l.product_name ILIKE %(pattern)s ESCAPE '\'
       OR l.variant_name ILIKE %(pattern)s ESCAPE '\'
       OR l.seller_sku ILIKE %(pattern)s ESCAPE '\')
  AND (%(before)s::uuid IS NULL OR (l.product_name, l.id) >
      (SELECT c.product_name, c.id FROM public.marketplace_listings c
       WHERE c.client_id = %(client_id)s AND c.id = %(before)s::uuid))
ORDER BY l.product_name, l.id LIMIT %(limit)s;
