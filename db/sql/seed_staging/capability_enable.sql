UPDATE public.capability_registry
SET is_enabled = true, client_enabled = true,
    client_enabled_changed_at = now(), client_enabled_changed_by = %s, updated_at = now()
WHERE client_id = %s AND NOT (is_enabled AND client_enabled);
