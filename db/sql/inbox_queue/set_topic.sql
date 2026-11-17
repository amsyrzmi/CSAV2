UPDATE public.conversations
SET topic = %s,
    inbox_version = inbox_version + 1
WHERE client_id = %s AND id = %s
RETURNING id;
