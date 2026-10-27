SELECT a.id, a.conversation_id, a.draft, a.status
FROM public.approvals a
JOIN public.conversations c ON c.client_id = a.client_id AND c.id = a.conversation_id
WHERE a.client_id = %s AND a.id = %s AND c.channel IN ('telegram','whatsapp')
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_threads t WHERE t.id = c.id)
  AND NOT EXISTS (SELECT 1 FROM public.console_chat_participants p WHERE p.contact_id = c.contact_id);
