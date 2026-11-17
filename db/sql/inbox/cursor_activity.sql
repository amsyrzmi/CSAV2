-- The sort value of a bare conversation id sent as the list cursor by older clients (the activity
-- order). No row: the id is not one of this company's conversations and the page is empty, as it
-- was before the queue list. Named parameters: client_id, conversation_id.
SELECT c.last_activity_at
FROM public.conversations c
WHERE c.client_id = %(client_id)s AND c.id = %(conversation_id)s;
