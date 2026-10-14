SELECT name, display_name, circuit_state, fail_count, opened_at
FROM public.capability_registry
WHERE client_id = %s
ORDER BY name;
