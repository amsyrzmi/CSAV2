SELECT id FROM capability_registry
WHERE client_id = %s AND name = %s AND is_enabled AND client_enabled;
