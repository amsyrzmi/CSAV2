-- provider_status, synced_at and buyer_cleared describe a synced TikTok order only: local orders
-- read NULL, NULL, false. The detail is tiktok_shop only on purpose; the local-flow guards'
-- synced-order predicate also includes shopee.
SELECT o.id, o.status, o.payment_status, o.total, o.currency, o.channel,
       o.external_order_id, o.external_shop_id, o.tracking_no, o.carrier,
       o.placed_at, o.shipped_at, o.created_at, o.updated_at,
       CASE WHEN s.synced THEN o.provider_status END AS provider_status,
       CASE WHEN s.synced THEN o.synced_at END AS synced_at,
       (s.synced AND o.buyer_cleared_at IS NOT NULL) AS buyer_cleared
FROM public.orders o
CROSS JOIN LATERAL (
    SELECT o.channel = 'tiktok_shop' AND o.external_order_id IS NOT NULL AS synced
) s
WHERE o.client_id = %s AND o.id = %s
  AND (%s::text IS NULL OR o.channel = %s)
  AND (%s::text IS NULL OR o.external_shop_id = %s);
