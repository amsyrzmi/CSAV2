UPDATE public.capability_registry
SET is_enabled = COALESCE(%s, is_enabled),
    client_enabled = COALESCE(%s, client_enabled),
    client_enabled_changed_at = CASE WHEN %s::boolean IS NOT NULL THEN now() ELSE client_enabled_changed_at END,
    client_enabled_changed_by = COALESCE(%s, client_enabled_changed_by),
    updated_at = now()
WHERE client_id = %s
  AND name = %s
RETURNING id, name, is_enabled, client_enabled, circuit_state;
