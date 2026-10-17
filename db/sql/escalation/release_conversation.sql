UPDATE public.conversations
SET status = 'open',
    owned_by = 'ai',
    ai_muted_until = NULL,
    ai_suppressed_before = clock_timestamp(), inbox_version = inbox_version + 1
WHERE id = %s
  AND client_id = %s
  AND status = 'escalated'
RETURNING id;
