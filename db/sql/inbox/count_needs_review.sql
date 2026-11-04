-- Conversations holding at least one live pending approval, for the whole tenant.
-- Ignores every list filter; same exclusions as the list. Named parameter: client_id.
SELECT count(DISTINCT a.conversation_id)::int AS n
FROM public.approvals a
JOIN public.conversations c ON c.client_id = a.client_id AND c.id = a.conversation_id
WHERE a.client_id = %(client_id)s AND a.status = 'pending' AND a.expires_at > now()
  AND c.channel IN ('telegram','whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p WHERE p.contact_id = c.contact_id);
