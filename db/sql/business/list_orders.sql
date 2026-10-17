SELECT id, status, payment_status, total, currency, channel,
       external_order_id, external_shop_id, tracking_no, carrier,
       placed_at, shipped_at, created_at, updated_at
FROM public.orders
WHERE client_id = %s
  AND (%s::text IS NULL OR (created_at, id) <
      (SELECT created_at, id FROM public.orders WHERE client_id = %s AND id = %s))
ORDER BY created_at DESC, id DESC LIMIT %s;
