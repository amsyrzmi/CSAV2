UPDATE public.ingress_events
SET status = 'received',
    dispatched_at = NULL,
    attempts = GREATEST(attempts - 1, 0),
    next_attempt_at = now() + interval '1 second'
WHERE id = %s
  AND client_id = %s
  AND status = 'dispatched';
