SELECT contact_id, endpoint_id FROM public.console_chat_participants
WHERE client_id=%s AND user_id=%s;
