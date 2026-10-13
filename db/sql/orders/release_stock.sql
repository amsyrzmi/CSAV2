UPDATE public.products
SET reserved_qty = GREATEST(reserved_qty - %s, 0)
WHERE id = %s
  AND client_id = %s
RETURNING id;
