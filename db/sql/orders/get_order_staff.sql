SELECT id, status, payment_status, total, currency, contact_id
FROM public.orders
WHERE client_id = %s
  AND id = %s
  AND NOT (channel IN ('tiktok_shop', 'shopee') AND external_order_id IS NOT NULL)
FOR UPDATE;
