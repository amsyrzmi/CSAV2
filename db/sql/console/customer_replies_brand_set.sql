UPDATE public.clients
SET brand_name = %s, updated_at = now()
WHERE id = %s;
