-- One order with its money breakdown and ship-to. Only a synced TikTok order (channel
-- tiktok_shop with a provider order id) carries them: for a local order subtotal, shipping_fee,
-- discount and ship_to read NULL, so buyer data stored on local orders is never exposed here.
-- ship_to is also NULL once the buyer details were cleared. Same filters as get_order.sql.
SELECT o.id, o.status, o.payment_status, o.total, o.currency, o.channel,
       o.external_order_id, o.external_shop_id, o.tracking_no, o.carrier,
       o.placed_at, o.shipped_at, o.created_at, o.updated_at,
       s.synced,
       CASE WHEN s.synced THEN o.provider_status END AS provider_status,
       CASE WHEN s.synced THEN o.synced_at END AS synced_at,
       (s.synced AND o.buyer_cleared_at IS NOT NULL) AS buyer_cleared,
       CASE WHEN s.synced THEN o.subtotal END AS subtotal,
       CASE WHEN s.synced THEN o.shipping_fee END AS shipping_fee,
       CASE WHEN s.synced THEN o.discount END AS discount,
       CASE WHEN s.synced AND o.buyer_cleared_at IS NULL THEN o.ship_to END AS ship_to
FROM public.orders o
CROSS JOIN LATERAL (
    SELECT o.channel = 'tiktok_shop' AND o.external_order_id IS NOT NULL AS synced
) s
WHERE o.client_id = %s AND o.id = %s
  AND (%s::text IS NULL OR o.channel = %s)
  AND (%s::text IS NULL OR o.external_shop_id = %s);
