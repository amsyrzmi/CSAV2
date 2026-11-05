-- One synced marketplace order, idempotent on (client_id, channel, external_order_id).
-- Newer wins: the conflict branch writes only when the stored provider time is missing or not
-- newer than the incoming one (an incoming order without a time cannot be proven older, so it
-- writes and keeps the stored time). No row back means the stored order is newer: stale.
-- Once buyer_cleared_at is set, ship_to stays NULL and is never written back.
-- contact_id stays NULL; products, stock and reservations are not touched.
INSERT INTO public.orders AS o (
    id, client_id, store_id, channel, external_order_id, external_shop_id,
    status, payment_status, provider_status, total, currency, subtotal, shipping_fee, discount,
    placed_at, shipped_at, tracking_no, carrier, ship_to, synced_at, provider_updated_at
) VALUES (
    %(id)s, %(client_id)s, %(store_id)s, %(channel)s, %(external_order_id)s, %(external_shop_id)s,
    %(status)s, %(payment_status)s, %(provider_status)s, %(total)s, %(currency)s, %(subtotal)s,
    %(shipping_fee)s, %(discount)s,
    %(placed_at)s, %(shipped_at)s, %(tracking_no)s, %(carrier)s, %(ship_to)s,
    %(synced_at)s, %(provider_updated_at)s
)
ON CONFLICT (client_id, channel, external_order_id)
    WHERE channel IN ('tiktok_shop', 'shopee') AND external_order_id IS NOT NULL
DO UPDATE SET
    store_id = EXCLUDED.store_id,
    external_shop_id = EXCLUDED.external_shop_id,
    status = EXCLUDED.status,
    payment_status = EXCLUDED.payment_status,
    provider_status = EXCLUDED.provider_status,
    total = EXCLUDED.total,
    currency = EXCLUDED.currency,
    subtotal = EXCLUDED.subtotal,
    shipping_fee = EXCLUDED.shipping_fee,
    discount = EXCLUDED.discount,
    placed_at = EXCLUDED.placed_at,
    shipped_at = COALESCE(EXCLUDED.shipped_at, o.shipped_at),
    tracking_no = EXCLUDED.tracking_no,
    carrier = EXCLUDED.carrier,
    ship_to = CASE WHEN o.buyer_cleared_at IS NULL THEN EXCLUDED.ship_to END,
    synced_at = EXCLUDED.synced_at,
    provider_updated_at = COALESCE(EXCLUDED.provider_updated_at, o.provider_updated_at),
    updated_at = now()
WHERE o.provider_updated_at IS NULL
   OR EXCLUDED.provider_updated_at IS NULL
   OR o.provider_updated_at <= EXCLUDED.provider_updated_at
RETURNING (xmax = 0) AS inserted;
