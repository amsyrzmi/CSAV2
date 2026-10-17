SELECT (c.ai_suppressed_before IS NULL OR i.created_at > c.ai_suppressed_before)
       AND (NOT ((c.owned_by = 'human_support' AND
           (c.ai_muted_until IS NULL OR c.ai_muted_until > now())) IS TRUE)
       OR (%s AND EXISTS (SELECT 1 FROM public.escalations e
           WHERE e.client_id = c.client_id AND e.conversation_id = c.id AND e.trace_id = %s))) AS allowed
FROM public.conversations c
LEFT JOIN public.ingress_events i ON i.client_id = c.client_id AND i.trace_id = %s
WHERE c.client_id = %s AND c.id = %s
FOR UPDATE OF c;
