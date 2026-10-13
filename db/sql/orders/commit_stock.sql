UPDATE public.products
SET stock_qty = GREATEST(stock_qty - %s, 0),
    reserved_qty = GREATEST(reserved_qty - %s, 0)
WHERE id = %s
  AND client_id = %s
RETURNING id;
