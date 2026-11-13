-- The turn of the latest assistant message of this chat before the current turn: a yes confirms
-- a sign-up only when that message was its read-back.
SELECT trace_id
FROM public.messages
WHERE client_id = %(client_id)s AND conversation_id = %(conversation_id)s
  AND direction = 'outbound'
  AND (trace_id IS NULL OR trace_id <> %(trace_id)s)
ORDER BY created_at DESC, id DESC
LIMIT 1;
