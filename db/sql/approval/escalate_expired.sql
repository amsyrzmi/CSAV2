INSERT INTO public.escalations
    (client_id, trace_id, conversation_id, contact_id, reason, priority)
SELECT client_id, %s, id, contact_id, 'approval_expired', 'high'
FROM public.conversations
WHERE client_id = %s AND id = %s
ON CONFLICT (client_id, trace_id) DO NOTHING;
