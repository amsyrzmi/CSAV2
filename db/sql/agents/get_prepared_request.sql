SELECT id, action, payload FROM agent_preparation_requests
WHERE client_id = %s AND contact_id = %s AND agent = %s AND idempotency_key = %s;
