INSERT INTO public.console_chat_turns
    (client_id, conversation_id, request_key, fingerprint, trace_id, body)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id, trace_id;
