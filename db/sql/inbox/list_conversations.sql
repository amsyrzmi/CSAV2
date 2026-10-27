SELECT c.id, c.channel, c.status, c.owned_by, c.ai_muted_until, c.inbox_version,
       c.opened_at,
       (c.owned_by = 'human_support' AND
        (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE AS human_muted
FROM public.conversations c
WHERE c.client_id = %s AND c.channel IN ('telegram','whatsapp')
  AND (%s::text IS NULL OR c.channel = %s)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p WHERE p.contact_id = c.contact_id)
  AND (%s::uuid IS NULL OR (c.opened_at, c.id) < (
      SELECT opened_at, id FROM public.conversations WHERE client_id = c.client_id AND id = %s))
ORDER BY c.opened_at DESC, c.id DESC LIMIT %s;
