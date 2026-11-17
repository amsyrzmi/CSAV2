-- Every escalation of the conversation that is not resolved yet ('open' or 'claimed').
UPDATE public.escalations
SET status = 'resolved'
WHERE client_id = %s AND conversation_id = %s AND status <> 'resolved'
RETURNING id;
