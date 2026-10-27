UPDATE public.conversations
SET last_inbound_at = GREATEST(COALESCE(last_inbound_at, %(at)s), %(at)s)
WHERE client_id = %(client_id)s AND id = %(conversation_id)s
