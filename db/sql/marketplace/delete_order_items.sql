-- A synced order's lines are replaced as a set on every accepted sync (order_items has no
-- provider key to match single rows on).
DELETE FROM public.order_items WHERE client_id = %(client_id)s AND order_id = %(order_id)s;
