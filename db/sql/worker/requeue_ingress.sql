UPDATE public.ingress_events
SET status = 'received',
    dispatched_at = NULL,
    next_attempt_at = now(),
    last_error = %s
WHERE id = %s
  AND client_id = %s
  AND status = %s
RETURNING id;
