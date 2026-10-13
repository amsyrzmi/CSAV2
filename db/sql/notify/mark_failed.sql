UPDATE public.staff_notifications
SET status = %s, locked_at = NULL, last_error = %s, next_attempt_at = %s
WHERE id = %s AND client_id = %s AND status = 'delivering'
RETURNING id;
