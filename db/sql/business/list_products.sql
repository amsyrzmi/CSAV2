SELECT id, sku, name, price, currency, stock_qty, reserved_qty, available_qty,
       is_active, created_at, updated_at
FROM public.products
WHERE client_id = %s
  AND (%s::uuid IS NULL OR (created_at, id) <
      (SELECT created_at, id FROM public.products WHERE client_id = %s AND id = %s))
ORDER BY created_at DESC, id DESC LIMIT %s;
