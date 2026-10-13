SELECT config -> 'policy' AS policy
FROM public.clients
WHERE id = %s;
