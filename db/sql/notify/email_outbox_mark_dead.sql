-- Terminal, whatever the attempt count: the recipient has no address to send to (left the company,
-- disabled, or a platform admin). Parameters: last_error, id, client_id.
UPDATE public.console_email_outbox
SET status = 'dead', locked_at = NULL, last_error = %s
WHERE id = %s AND client_id = %s AND status = 'delivering'
RETURNING id;
