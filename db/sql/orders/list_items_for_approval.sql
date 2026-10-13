SELECT product_id, qty
FROM public.order_items
WHERE client_id = %s
  AND order_id = %s
ORDER BY product_id;
