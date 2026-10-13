UPDATE public.conversations
SET turn_count = turn_count + 1
WHERE id = %s
  AND client_id = %s
RETURNING id;
