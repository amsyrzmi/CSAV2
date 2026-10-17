UPDATE public.outbound_deliveries
SET status = 'skipped', last_error = 'human_takeover', locked_at = NULL
WHERE client_id = %s AND id = %s AND status = 'delivering'
RETURNING id;
