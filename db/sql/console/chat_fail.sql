UPDATE public.console_chat_turns SET failed=true
WHERE client_id=%s AND trace_id=%s
RETURNING id;
