SELECT id, actor, action, from_status, to_status, payload, created_at
FROM public.subscription_events
WHERE client_id = %(client_id)s AND subscription_id = %(subscription_id)s
ORDER BY created_at DESC, id DESC
LIMIT 100;
