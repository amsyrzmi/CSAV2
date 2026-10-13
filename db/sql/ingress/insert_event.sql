INSERT INTO public.ingress_events
    (client_id, endpoint_id, contact_id, idempotency_key, payload_hash, trace_id, raw)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (client_id, idempotency_key) DO NOTHING
RETURNING id
