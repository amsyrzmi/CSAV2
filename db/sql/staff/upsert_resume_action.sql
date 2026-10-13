INSERT INTO public.staff_actions
    (client_id, notification_id, target_id, conversation_id, action, expires_at)
VALUES (%s, %s, %s, %s, 'resume', %s)
ON CONFLICT (notification_id, action) DO UPDATE SET action = EXCLUDED.action
RETURNING id;
