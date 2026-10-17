UPDATE public.conversations
SET status = 'escalated',
    owned_by = 'human_support',
    ai_muted_until = %s,
    inbox_version = inbox_version + 1
WHERE id = %s
  AND client_id = %s
RETURNING id;
