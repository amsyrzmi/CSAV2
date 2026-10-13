SELECT trace_id, payload_hash
FROM public.ingress_events
WHERE client_id = %s
  AND idempotency_key = %s
