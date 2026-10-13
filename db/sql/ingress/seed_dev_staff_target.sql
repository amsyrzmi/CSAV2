INSERT INTO public.staff_notification_targets (client_id, channel, address, verified_at)
VALUES (%s, 'telegram', %s, now())
ON CONFLICT (client_id, channel, address) DO NOTHING;
