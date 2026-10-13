UPDATE public.staff_notifications
SET status = CASE WHEN attempts >= %s THEN 'dead' ELSE 'pending' END,
    locked_at = NULL,
    last_error = 'notification lease expired'
WHERE status = 'delivering'
  AND locked_at < now() - make_interval(secs => %s)
RETURNING id, client_id, status;
