UPDATE public.staff_notifications
SET status = 'pending', locked_at = NULL, attempts = attempts - 1, next_attempt_at = %s,
    held_at = now()
WHERE id = %s AND client_id = %s AND status = 'delivering'
RETURNING id;
