SELECT sku, name, description, price, currency, available_qty, unit
FROM public.products
WHERE client_id = %s
  AND is_active
  AND sku = %s;
