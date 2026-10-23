SELECT id, slug, name, status, config
FROM public.clients
WHERE slug = %s OR lower(name) = lower(%s)
ORDER BY id LIMIT 2;
