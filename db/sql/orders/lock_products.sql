SELECT id, sku, name, price, currency, available_qty, is_active
FROM public.products
WHERE client_id = %s
  AND sku = ANY(%s)
ORDER BY sku
FOR UPDATE;
