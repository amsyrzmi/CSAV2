UPDATE public.conversations
SET last_inbound_at = now()
WHERE client_id = %s AND id = %s
