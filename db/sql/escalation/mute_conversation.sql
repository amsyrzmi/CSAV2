UPDATE public.conversations
SET status = 'escalated',
    owned_by = 'human_support',
    ai_muted_until = %s
WHERE id = %s
  AND client_id = %s
RETURNING id;
