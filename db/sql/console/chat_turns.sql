SELECT t.id, t.trace_id, t.request_key, t.body, t.created_at,
       CASE
           WHEN t.failed THEN 'failed'
           WHEN EXISTS (SELECT 1 FROM public.outbound_deliveries d
               WHERE d.client_id=t.client_id AND d.trace_id=t.trace_id
                 AND d.conversation_id=t.conversation_id AND d.status='dead')
               OR e.status='dead' THEN 'failed'
           WHEN t.held THEN 'held'
           WHEN EXISTS (SELECT 1 FROM public.outbound_deliveries d
               WHERE d.client_id=t.client_id AND d.trace_id=t.trace_id
                 AND d.conversation_id=t.conversation_id
                 AND d.status IN ('pending','delivering')) THEN 'awaiting_delivery'
           WHEN EXISTS (SELECT 1 FROM public.outbound_deliveries d
               WHERE d.client_id=t.client_id AND d.trace_id=t.trace_id
                 AND d.conversation_id=t.conversation_id AND d.status='delivered')
               THEN 'available'
           WHEN e.status='completed' AND NOT EXISTS (
               SELECT 1 FROM public.outbound_deliveries d
               WHERE d.client_id=t.client_id AND d.trace_id=t.trace_id
                 AND d.conversation_id=t.conversation_id
           ) THEN 'no_reply'
           WHEN e.status='completed' THEN 'awaiting_delivery'
           WHEN e.status='dispatched' THEN 'processing'
           ELSE 'queued'
       END AS status,
       COALESCE((
           SELECT jsonb_agg(jsonb_build_object('id', m.id, 'body', m.redacted_body)
               ORDER BY m.created_at, m.id)
           FROM public.messages m
           JOIN public.outbound_deliveries d ON d.message_id=m.id AND d.client_id=m.client_id
           WHERE m.client_id=t.client_id AND m.trace_id=t.trace_id
             AND m.conversation_id=t.conversation_id AND m.direction='outbound'
             AND d.conversation_id=t.conversation_id AND d.trace_id=t.trace_id
             AND d.status='delivered'
       ), '[]'::jsonb) AS replies
FROM public.console_chat_turns t
JOIN public.ingress_events e ON e.trace_id=t.trace_id AND e.client_id=t.client_id
WHERE t.client_id=%s AND t.conversation_id=%s
  AND (%s::bigint IS NULL OR t.id < %s)
ORDER BY t.id DESC LIMIT %s;
