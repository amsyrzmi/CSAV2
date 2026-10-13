SELECT sku, name, price, currency, available_qty
FROM public.products
WHERE client_id = %s
  AND is_active
  AND (name ILIKE %s OR sku ILIKE %s OR coalesce(category, '') ILIKE %s)
ORDER BY name
LIMIT 5;
