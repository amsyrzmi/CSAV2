UPDATE public.clients
SET brand_name = %s, region = %s, languages = %s, updated_at = now()
WHERE id = %s;
