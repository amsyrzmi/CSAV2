-- Lazy sweep: an owner's requested proposals past expiry become expired.
UPDATE public.console_proposals
SET status = 'expired'
WHERE client_id = %s AND user_id = %s AND status = 'requested' AND expires_at <= now()
RETURNING id;
