INSERT INTO public.order_items
    (client_id, order_id, product_id, sku, name_snapshot, unit_price, qty, currency)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
