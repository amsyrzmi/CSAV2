SELECT id, trace_id, fingerprint
FROM public.console_chat_turns
WHERE client_id = %s AND conversation_id = %s AND request_key = %s;
