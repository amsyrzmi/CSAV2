INSERT INTO public.ingress_events
    (client_id, endpoint_id, idempotency_key, payload_hash, trace_id, status, completed_at)
VALUES (%s, %s, %s, %s, %s, 'completed', now())
ON CONFLICT (client_id, idempotency_key) DO NOTHING
RETURNING id
