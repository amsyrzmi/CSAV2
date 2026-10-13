SELECT product_id, sku, name_snapshot AS name, qty, unit_price
FROM public.order_items
WHERE client_id = %s
  AND order_id = %s
ORDER BY sku;
