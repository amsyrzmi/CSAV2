INSERT INTO public.messages
    (client_id, conversation_id, direction, author, body, redacted_body,
     idempotency_key, trace_id, send_authority)
VALUES (%s, %s, 'outbound', 'human_support', %s, %s, %s, %s, 'human') RETURNING id;
