INSERT INTO agent_preparation_requests
    (client_id, contact_id, agent, action, idempotency_key, payload, trace_id, requested_by)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (client_id, contact_id, agent, idempotency_key) DO NOTHING
RETURNING id, action, payload;
