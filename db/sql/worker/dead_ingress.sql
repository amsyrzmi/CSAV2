UPDATE public.ingress_events
SET status = 'dead',
    dispatched_at = NULL,
    last_error = %s
WHERE id = %s
  AND client_id = %s
  AND status = %s
RETURNING id;
