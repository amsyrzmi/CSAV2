-- created_at is clock_timestamp(), not the now() default: deliveries written in one transaction
-- are claimed in the order they were written (delivery/claim.sql orders by created_at, id).
INSERT INTO public.outbound_deliveries
    (client_id, endpoint_id, trace_id, conversation_id, message_id, created_at)
VALUES (%s, %s, %s, %s, %s, clock_timestamp())
ON CONFLICT (message_id) DO NOTHING
RETURNING id;
