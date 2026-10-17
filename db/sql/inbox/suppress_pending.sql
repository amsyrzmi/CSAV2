UPDATE public.outbound_deliveries d
SET status = 'skipped', last_error = 'human_takeover', locked_at = NULL
FROM public.messages m
WHERE d.client_id = %s AND d.conversation_id = %s AND d.status = 'pending'
  AND m.id = d.message_id AND m.client_id = d.client_id
  AND m.send_authority IN ('automation', 'handoff');
