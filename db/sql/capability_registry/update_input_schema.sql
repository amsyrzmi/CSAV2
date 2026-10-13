UPDATE capability_registry
SET input_schema = %s
WHERE client_id = %s AND name = %s
RETURNING id;
