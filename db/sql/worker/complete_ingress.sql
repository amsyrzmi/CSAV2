UPDATE public.ingress_events
SET status = 'completed',
    completed_at = now(),
    dispatched_at = NULL,
    last_error = NULL
WHERE id = %s
  AND client_id = %s
  AND status = 'dispatched'
RETURNING id;
