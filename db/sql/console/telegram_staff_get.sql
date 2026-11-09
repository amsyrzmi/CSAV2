SELECT address, verified_at
FROM public.staff_notification_targets
WHERE client_id = %s AND channel = 'telegram' AND is_enabled
ORDER BY created_at DESC
LIMIT 1;
