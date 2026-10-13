SELECT id, client_id, trace_id, raw, status, attempts, last_error
FROM public.ingress_events
WHERE (status = 'dispatched' AND dispatched_at < now() - make_interval(secs => %s))
   OR (status = 'failed' AND next_attempt_at <= now())
ORDER BY COALESCE(dispatched_at, next_attempt_at), created_at, id
LIMIT %s
FOR UPDATE SKIP LOCKED;
