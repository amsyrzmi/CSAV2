-- Where a message to a booking's customer goes: the Telegram chat of that customer, by the shape of
-- inbox/get_reply_route.sql. The booking's own chat comes first, else the customer's latest chat.
-- The trace is one the chat's own message came in on, because the delivery worker only sends on a
-- trace its route and message agree on. Another contact's chat is never a candidate, and neither
-- is a WhatsApp one: nothing proactive goes there.
SELECT c.id AS conversation_id, i.trace_id, i.endpoint_id
FROM public.conversations c
JOIN public.messages m ON m.client_id = c.client_id AND m.conversation_id = c.id
                      AND m.direction = 'inbound'
JOIN public.ingress_events i ON i.trace_id = m.trace_id AND i.client_id = m.client_id
JOIN public.webhook_endpoints e ON e.id = i.endpoint_id AND e.client_id = i.client_id
JOIN public.channel_credentials k ON k.id = e.credential_id AND k.client_id = e.client_id
WHERE c.client_id = %(client_id)s AND c.contact_id = %(contact_id)s AND c.channel = 'telegram'
  AND i.contact_id = c.contact_id AND k.channel = 'telegram' AND e.is_enabled
ORDER BY coalesce(c.id = %(conversation_id)s::uuid, false) DESC, m.created_at DESC, m.id DESC
LIMIT 1;
