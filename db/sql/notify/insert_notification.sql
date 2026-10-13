INSERT INTO public.staff_notifications
    (client_id, target_id, event, priority, payload, idempotency_key)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (client_id, idempotency_key) DO NOTHING
RETURNING id;
