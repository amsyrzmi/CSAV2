SELECT id, sequence, archived_at, created_at
FROM public.console_chat_threads
WHERE client_id = %s AND user_id = %s AND request_key = %s;
