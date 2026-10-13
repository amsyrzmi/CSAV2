UPDATE public.products
SET reserved_qty = reserved_qty + %s
WHERE id = %s
  AND client_id = %s
RETURNING id;
