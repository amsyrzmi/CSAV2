SELECT i.endpoint_id
FROM public.ingress_events i
WHERE i.client_id = %s AND i.trace_id = %s AND i.endpoint_id = %s;
