UPDATE public.staff_notification_targets
SET is_enabled = false
WHERE client_id = %s AND channel = 'telegram' AND is_enabled
RETURNING id;
