-- TikTok listings matching a customer's words: product name, variant, the two together, or the
-- seller SKU. The pattern is LIKE-escaped by the caller (reads.escape_like). Only listings whose
-- product status is in reads.SELLABLE_PRODUCT_STATUSES match (an allowlist: drafts, deactivated,
-- frozen and removed products never do). exact = the words are the whole SKU or the whole
-- "name variant", so one listing can win among several partial matches.
SELECT l.id, l.store_id, l.external_product_id, l.product_name,
       coalesce(l.variant_name, '') AS variant_name, l.seller_sku,
       (lower(coalesce(l.seller_sku, '')) = lower(%(words)s)
        OR lower(l.product_name || coalesce(' ' || nullif(l.variant_name, ''), ''))
           = lower(%(words)s)) AS exact
FROM public.marketplace_listings l
WHERE l.client_id = %(client_id)s AND l.channel = 'tiktok_shop'
  AND upper(coalesce(l.product_status, '')) = ANY(%(sellable)s::text[])
  AND (l.product_name ILIKE %(pattern)s ESCAPE '\'
       OR l.variant_name ILIKE %(pattern)s ESCAPE '\'
       OR l.seller_sku ILIKE %(pattern)s ESCAPE '\'
       OR (l.product_name || ' ' || coalesce(l.variant_name, '')) ILIKE %(pattern)s ESCAPE '\')
ORDER BY exact DESC, l.product_name, l.variant_name, l.id
LIMIT %(limit)s;
