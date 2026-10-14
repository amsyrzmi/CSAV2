SELECT redacted_body
FROM public.messages
WHERE client_id = %s
  AND conversation_id = %s
  AND direction = 'inbound'
ORDER BY created_at DESC
LIMIT 5;
