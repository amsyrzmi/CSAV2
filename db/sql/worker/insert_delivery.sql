INSERT INTO public.outbound_deliveries
    (client_id, endpoint_id, trace_id, conversation_id, message_id)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (message_id) DO NOTHING
RETURNING id;
