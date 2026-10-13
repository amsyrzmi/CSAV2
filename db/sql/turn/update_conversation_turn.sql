UPDATE public.conversations
SET clarify_count = %s,
    last_intent = %s
WHERE id = %s
  AND client_id = %s
RETURNING id;
