SELECT channel, address, quiet_hours, language
FROM public.staff_notification_targets
WHERE client_id = %s
  AND id = %s
  AND is_enabled;
