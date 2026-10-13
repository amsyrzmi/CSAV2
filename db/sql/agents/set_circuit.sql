UPDATE public.capability_registry
SET circuit_state = %s,
    fail_count = %s,
    opened_at = %s
WHERE client_id = %s
  AND name = %s
RETURNING id;
