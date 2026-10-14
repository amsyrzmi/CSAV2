UPDATE public.ingress_events
SET status = 'received',
    attempts = 0,
    next_attempt_at = now(),
    last_error = NULL
WHERE id = %s
  AND client_id = %s
RETURNING id, client_id, status, attempts;
