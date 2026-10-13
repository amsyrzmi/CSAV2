INSERT INTO public.products (client_id, sku, name, price, stock_qty)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (client_id, sku) DO NOTHING;
