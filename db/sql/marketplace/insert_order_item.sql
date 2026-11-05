-- One folded line of a synced order; run after delete_order_items.sql in the same transaction.
-- product_id stays NULL: a marketplace line never reserves or moves local stock.
INSERT INTO public.order_items
    (client_id, order_id, product_id, sku, name_snapshot, unit_price, qty, currency,
     external_line_id)
VALUES
    (%(client_id)s, %(order_id)s, NULL, %(sku)s, %(name_snapshot)s, %(unit_price)s, %(qty)s,
     %(currency)s, %(external_line_id)s);
