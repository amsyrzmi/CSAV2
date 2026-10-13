UPDATE public.outbound_deliveries
SET status = 'dead',
    locked_at = NULL,
    last_error = 'delivery lease expired'
WHERE id = %s
  AND client_id = %s
  AND status = 'delivering'
RETURNING id;
