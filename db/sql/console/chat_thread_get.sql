SELECT t.id, t.sequence, t.contact_id, t.endpoint_id, t.archived_at, t.created_at,
       c.turn_count, c.status
FROM public.console_chat_threads t
JOIN public.conversations c ON c.id = t.id AND c.client_id = t.client_id
WHERE t.client_id = %s AND t.user_id = %s AND t.id = %s
FOR UPDATE OF t;
