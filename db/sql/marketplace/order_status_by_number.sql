-- A customer's TikTok order number (spec 6.7): matched on the provider's own number
-- (external_order_id), never on our order id. Only the columns the answer may use are selected:
-- no buyer, address, ship-to, items or totals. A disconnected store's orders still answer while
-- retained (the answer holds no buyer data).
SELECT status, tracking_no, carrier, provider_updated_at
FROM public.orders
WHERE client_id = %(client_id)s
  AND channel = 'tiktok_shop'
  AND external_order_id = %(order_number)s
LIMIT 1;
