-- The asked listing (any status) of this tenant, for a stock read.
SELECT id, store_id, external_product_id, product_name, product_status, synced_at
FROM public.marketplace_listings
WHERE client_id = %(client_id)s AND id = %(listing_id)s AND channel = 'tiktok_shop';
