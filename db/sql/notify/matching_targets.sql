SELECT id, channel
FROM public.staff_notification_targets
WHERE client_id = %s
  AND is_enabled
  AND %s = ANY (events)
  AND array_position(ARRAY['low', 'normal', 'high', 'urgent'], min_priority)
      <= array_position(ARRAY['low', 'normal', 'high', 'urgent'], %s);
