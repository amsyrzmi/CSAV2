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
WHERE o.client_id = %s
  AND (%s::text IS NULL OR o.channel = %s)
  AND (%s::text IS NULL OR o.external_shop_id = %s)
  AND (%s::timestamptz IS NULL OR o.created_at >= %s)
  AND (%s::timestamptz IS NULL OR o.created_at < %s)
  AND (%s::text IS NULL OR o.status = %s)
  AND (%s::text IS NULL OR o.payment_status = %s)
  AND (%s::text IS NULL OR (o.created_at, o.id) <
      (SELECT created_at, id FROM public.orders WHERE client_id = %s AND id = %s))
ORDER BY o.created_at DESC, o.id DESC LIMIT %s;
