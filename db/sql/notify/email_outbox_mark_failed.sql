-- A failed attempt: back to pending with a delay, or dead at the attempt cap (status is chosen by
-- the caller). Parameters: status, last_error (a category, never an address), next_attempt_at, id,
-- client_id.
UPDATE public.console_email_outbox
SET status = %s, locked_at = NULL, last_error = %s, next_attempt_at = %s
WHERE id = %s AND client_id = %s AND status = 'delivering'
RETURNING id;
