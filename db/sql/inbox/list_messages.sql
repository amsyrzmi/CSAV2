SELECT m.id, m.direction, m.author, coalesce(m.redacted_body, m.body) AS body,
       m.created_at, m.trace_id, d.status AS delivery_status
FROM public.messages m
LEFT JOIN public.outbound_deliveries d ON d.message_id = m.id AND d.client_id = m.client_id
WHERE m.client_id = %s AND m.conversation_id = %s
  AND (%s::uuid IS NULL OR (m.created_at, m.id) < (
      SELECT created_at,id FROM public.messages WHERE client_id = m.client_id
      AND conversation_id = m.conversation_id AND id = %s))
ORDER BY m.created_at DESC, m.id DESC LIMIT %s;
