INSERT INTO public.messages
    (client_id, conversation_id, direction, author, body, redacted_body,
     attachments, has_attachment, idempotency_key, trace_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING id;
