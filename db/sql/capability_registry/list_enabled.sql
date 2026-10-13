SELECT *
FROM capability_registry
WHERE client_id = %s
  AND is_enabled
  AND client_enabled
  AND circuit_state <> 'open'
ORDER BY name;
