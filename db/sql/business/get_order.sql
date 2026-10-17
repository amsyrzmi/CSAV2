SELECT id, status, payment_status, total, currency, channel,
       external_order_id, external_shop_id, tracking_no, carrier,
       placed_at, shipped_at, created_at, updated_at
FROM public.orders WHERE client_id = %s AND id = %s;
