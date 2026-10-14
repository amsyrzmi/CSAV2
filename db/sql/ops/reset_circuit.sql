UPDATE public.capability_registry
SET circuit_state = 'closed',
    fail_count = 0,
    opened_at = NULL
WHERE client_id = %s
  AND name = %s
RETURNING name, circuit_state;
