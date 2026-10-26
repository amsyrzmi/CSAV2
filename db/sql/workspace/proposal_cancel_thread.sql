UPDATE public.console_proposals
SET status = 'cancelled'
WHERE client_id = %s AND user_id = %s AND thread_id = %s AND status = 'requested'
RETURNING id, kind;
