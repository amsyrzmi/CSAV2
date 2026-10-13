UPDATE public.ingress_events
SET status = %s,
    dispatched_at = NULL,
    next_attempt_at = %s,
    last_error = %s
WHERE id = %s
  AND client_id = %s
  AND status = 'dispatched'
RETURNING id;
