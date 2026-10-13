UPDATE public.outbound_deliveries
SET status = 'pending',
    locked_at = NULL,
    next_attempt_at = %s,
    last_error = 'delivery lease expired'
WHERE id = %s
  AND client_id = %s
  AND status = 'delivering'
RETURNING id;
