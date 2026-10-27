-- Records an event as handled without queueing work (status completed, never claimed).
INSERT INTO public.ingress_events
    (client_id, endpoint_id, contact_id, idempotency_key, payload_hash, trace_id, raw,
     status, completed_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed', now())
ON CONFLICT (client_id, idempotency_key) DO NOTHING
RETURNING id
