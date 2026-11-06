-- The lines of one order; the caller only asks for a synced TikTok order.
SELECT sku, name_snapshot AS name, qty, unit_price
FROM public.order_items
WHERE client_id = %s AND order_id = %s
ORDER BY sku, name_snapshot, unit_price, id;
