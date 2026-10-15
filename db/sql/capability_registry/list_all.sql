SELECT *
FROM public.capability_registry
WHERE client_id = %s
ORDER BY name;
