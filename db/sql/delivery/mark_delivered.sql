UPDATE public.outbound_deliveries
SET status = 'delivered',
    delivered_at = now(),
    locked_at = NULL,
    last_error = NULL,
    last_status_code = %s
WHERE id = %s
  AND client_id = %s
  AND status = 'delivering'
RETURNING id;
