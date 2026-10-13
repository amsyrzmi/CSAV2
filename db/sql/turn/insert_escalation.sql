INSERT INTO public.escalations
    (client_id, trace_id, conversation_id, contact_id, reason, priority)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (client_id, trace_id) DO NOTHING
RETURNING id;
