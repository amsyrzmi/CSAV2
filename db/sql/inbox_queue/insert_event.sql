-- One queue history row. client_id and conversation_id come from the conversation row itself
-- (under row security), never from separate parameters.
INSERT INTO public.inbox_queue_events
    (client_id, conversation_id, actor_user_id, action, from_status, to_status, details)
SELECT c.client_id, c.id, %(actor_user_id)s::uuid, %(action)s, %(from_status)s, %(to_status)s,
       %(details)s
FROM public.conversations c
WHERE c.client_id = %(client_id)s AND c.id = %(conversation_id)s
RETURNING id;
