SELECT id, conversation_id
FROM public.messages
WHERE client_id = %s
  AND idempotency_key = %s;
