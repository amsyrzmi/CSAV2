INSERT INTO public.staff_actions
    (client_id, notification_id, target_id, order_id, action, expires_at)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (notification_id, action) DO UPDATE SET action = EXCLUDED.action
RETURNING id;
