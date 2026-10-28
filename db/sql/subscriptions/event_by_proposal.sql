-- The event a subscription_action proposal already recorded (at most one: unique index).
SELECT id, subscription_id, to_status, payload
FROM public.subscription_events
WHERE client_id = %(client_id)s AND proposal_id = %(proposal_id)s;
