UPDATE public.conversations
SET status = 'open',
    owned_by = 'ai',
    ai_muted_until = NULL
WHERE id = %s
  AND client_id = %s
  AND status = 'escalated'
RETURNING id;
