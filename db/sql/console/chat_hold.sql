UPDATE public.console_chat_turns SET held=true, hold_reason=%s, held_draft=%s
WHERE client_id=%s AND trace_id=%s
RETURNING id;
