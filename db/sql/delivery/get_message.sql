SELECT delivery.id AS delivery_id,
       delivery.client_id,
       delivery.endpoint_id,
       delivery.trace_id,
       delivery.conversation_id,
       delivery.message_id,
       message.body,
       message.ui,
       message.send_authority,
       conversation.channel,
       conversation.channel_thread,
       conversation.last_inbound_at
FROM public.outbound_deliveries AS delivery
JOIN public.messages AS message
  ON message.id = delivery.message_id
 AND message.client_id = delivery.client_id
JOIN public.conversations AS conversation
  ON conversation.id = delivery.conversation_id
 AND conversation.client_id = delivery.client_id
 AND (conversation.channel NOT IN ('telegram','whatsapp') OR (
     message.conversation_id = conversation.id
     AND message.trace_id IS NOT DISTINCT FROM delivery.trace_id
 ))
WHERE delivery.id = %s
  AND delivery.client_id = %s
  AND delivery.status = 'delivering';
