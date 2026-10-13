SELECT endpoint_id
FROM public.ingress_events
WHERE trace_id = %s
  AND client_id = %s
ORDER BY created_at
LIMIT 1;
