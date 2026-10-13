SELECT delivery.id AS delivery_id,
       delivery.client_id,
       delivery.endpoint_id,
       delivery.trace_id,
       delivery.conversation_id,
       delivery.message_id,
       message.body,
       message.ui,
       conversation.channel,
       conversation.channel_thread
FROM public.outbound_deliveries AS delivery
JOIN public.messages AS message
  ON message.id = delivery.message_id
 AND message.client_id = delivery.client_id
JOIN public.conversations AS conversation
  ON conversation.id = delivery.conversation_id
 AND conversation.client_id = delivery.client_id
WHERE delivery.id = %s
  AND delivery.client_id = %s
  AND delivery.status = 'delivering';
