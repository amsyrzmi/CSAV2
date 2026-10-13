SELECT channel, address, quiet_hours
FROM public.staff_notification_targets
WHERE client_id = %s
  AND id = %s
  AND is_enabled;
