-- created_at is clock_timestamp(), not the now() default (the transaction's start): rows written in
-- one transaction (an image reply, then its text) keep the order they were written in, which the
-- delivery claim and the Inbox read through (created_at, id).
INSERT INTO public.messages
    (client_id, conversation_id, direction, author, body, redacted_body,
     attachments, has_attachment, idempotency_key, trace_id, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING id;
