SELECT i.trace_id, i.endpoint_id
FROM public.messages m
JOIN public.ingress_events i ON i.trace_id = m.trace_id AND i.client_id = m.client_id
JOIN public.conversations c ON c.id = m.conversation_id AND c.client_id = m.client_id
JOIN public.webhook_endpoints e ON e.id = i.endpoint_id AND e.client_id = i.client_id
JOIN public.channel_credentials k ON k.id = e.credential_id AND k.client_id = e.client_id
WHERE m.client_id = %s AND m.conversation_id = %s AND m.direction = 'inbound'
  AND i.contact_id = c.contact_id AND c.channel = 'telegram'
  AND k.channel = 'telegram' AND e.is_enabled
ORDER BY m.created_at DESC, m.id DESC LIMIT 1;
