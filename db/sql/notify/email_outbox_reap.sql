-- Stale person-email leases: back to pending, or dead at the attempt cap. Parameters: cap, seconds.
UPDATE public.console_email_outbox
SET status = CASE WHEN attempts >= %s THEN 'dead' ELSE 'pending' END,
    locked_at = NULL,
    last_error = 'notification lease expired'
WHERE status = 'delivering'
  AND locked_at < now() - make_interval(secs => %s)
RETURNING id, client_id, user_id, status;
