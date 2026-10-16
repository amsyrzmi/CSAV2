UPDATE public.console_chat_threads SET archived_at = now()
WHERE id = %s AND client_id = %s AND user_id = %s AND archived_at IS NULL
RETURNING id;
