-- Where a proactive message to a contact goes on WhatsApp: the contact's latest WhatsApp chat with
-- a message from the customer newer than 23 hours before now (inside WhatsApp's 24-hour window,
-- with an hour to spare). The joins are those of bookings/customer_route.sql: the trace is one
-- the chat's own message came in on, on an enabled endpoint of a WhatsApp credential, and another
-- contact's chat is never a candidate. now is the job's clock, else the database's.
SELECT c.id AS conversation_id, i.trace_id, i.endpoint_id
FROM public.conversations c
JOIN public.messages m ON m.client_id = c.client_id AND m.conversation_id = c.id
                      AND m.direction = 'inbound'
JOIN public.ingress_events i ON i.trace_id = m.trace_id AND i.client_id = m.client_id
JOIN public.webhook_endpoints e ON e.id = i.endpoint_id AND e.client_id = i.client_id
JOIN public.channel_credentials k ON k.id = e.credential_id AND k.client_id = e.client_id
WHERE c.client_id = %(client_id)s AND c.contact_id = %(contact_id)s AND c.channel = 'whatsapp'
  AND i.contact_id = c.contact_id AND k.channel = 'whatsapp' AND e.is_enabled
  AND m.created_at > coalesce(%(now)s::timestamptz, now()) - interval '23 hours'
ORDER BY m.created_at DESC, m.id DESC
LIMIT 1;
