-- Stored SKUs of one TikTok product in one store, our copy. With sellable_only, only listings
-- whose product status is in reads.SELLABLE_PRODUCT_STATUSES (an allowlist: fail closed);
-- without it, every stored SKU (used to name the SKUs of a live read). With exclude_removed,
-- never a SKU TikTok stopped listing (processor.REMOVED_PRODUCT_STATUS, set by the sync; its
-- stock_qty is stale): the staff copy read shows every other status.
SELECT id, external_sku_id, product_name, coalesce(variant_name, '') AS variant_name,
       seller_sku, stock_qty, price, currency, synced_at
FROM public.marketplace_listings
WHERE client_id = %(client_id)s AND store_id = %(store_id)s AND channel = 'tiktok_shop'
  AND external_product_id = %(product_id)s
  AND (NOT %(sellable_only)s::boolean
       OR upper(coalesce(product_status, '')) = ANY(%(sellable)s::text[]))
  AND (NOT %(exclude_removed)s::boolean
       OR upper(coalesce(product_status, '')) <> upper(%(removed)s))
ORDER BY variant_name, external_sku_id;
