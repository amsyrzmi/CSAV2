UPDATE public.console_email_outbox
SET status = 'delivered', delivered_at = now(), locked_at = NULL, last_error = NULL
WHERE id = %s AND client_id = %s AND status = 'delivering'
RETURNING id;
