INSERT INTO public.console_chat_threads
    (id, client_id, user_id, contact_id, endpoint_id, request_key)
VALUES (%s, %s, %s, %s, %s, %s)
RETURNING id, sequence, archived_at, created_at;
