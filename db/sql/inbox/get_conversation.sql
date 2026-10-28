SELECT c.id, c.channel, c.status, c.owned_by, c.ai_muted_until, c.inbox_version,
       c.opened_at,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted
FROM public.conversations c
WHERE c.client_id = %s AND c.id = %s AND c.channel IN ('telegram','whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p WHERE p.contact_id = c.contact_id);
