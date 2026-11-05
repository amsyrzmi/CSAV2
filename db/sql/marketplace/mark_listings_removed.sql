-- After a product's SKUs are upserted: its stored SKUs that TikTok no longer lists are marked
-- removed (product_status set to REMOVED_PRODUCT_STATUS from csa/marketplace/processor.py),
-- never deleted. A row stored from a newer provider version than this read is left alone.
UPDATE public.marketplace_listings AS l
SET product_status = %(removed)s, synced_at = %(now)s
WHERE l.client_id = %(client_id)s AND l.store_id = %(store_id)s AND l.channel = 'tiktok_shop'
  AND l.external_product_id = %(product_id)s
  AND NOT (l.external_sku_id = ANY(%(sku_ids)s::text[]))
  AND l.product_status IS DISTINCT FROM %(removed)s
  AND (l.provider_updated_at IS NULL OR %(updated_at)s::timestamptz IS NULL
       OR l.provider_updated_at <= %(updated_at)s::timestamptz)
RETURNING l.id;
