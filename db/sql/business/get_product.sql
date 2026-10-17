SELECT id, sku, name, price, currency, stock_qty, reserved_qty, available_qty,
       is_active, created_at, updated_at
FROM public.products WHERE client_id = %s AND id = %s;
