SELECT jsonb_build_object(
           'delivery_id', delivery.id,
           'message_id', message.id,
           'conversation_id', delivery.conversation_id,
           'channel', conversation.channel,
           'channel_thread', conversation.channel_thread,
           'body', message.redacted_body
       ) AS envelope
FROM public.outbound_deliveries AS delivery
JOIN public.messages AS message
  ON message.id = delivery.message_id
JOIN public.conversations AS conversation
  ON conversation.id = delivery.conversation_id
WHERE delivery.id = %s
  AND delivery.client_id = %s;
