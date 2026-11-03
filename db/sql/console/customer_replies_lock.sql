SELECT brand_name, config -> 'policy' AS policy
FROM public.clients
WHERE id = %s
FOR UPDATE;
