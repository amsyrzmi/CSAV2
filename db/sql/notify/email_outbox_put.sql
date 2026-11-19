-- Queue one person email for each console notification just written (put_console_notification.sql)
-- whose recipient has saved email on, lists the notification's kind and has a priority floor at or
-- below its priority (low < normal < high < urgent). No saved row means email off. Runs on the
-- caller's company transaction, so the email row commits or rolls back with the console row; the
-- unique notification id makes a second run for the same notification a no-op. Ids and keys only:
-- the address is looked up when the worker sends. Named parameters: client_id, notification_ids.
-- Returns the id of each outbox row written.
INSERT INTO public.console_email_outbox
    (client_id, user_id, notification_id, kind, priority, message_key, params, link)
SELECT n.client_id, n.user_id, n.id, n.kind, n.priority, n.message_key, n.params, n.link
FROM public.console_notifications n
JOIN public.console_notification_prefs p
  ON p.client_id = n.client_id AND p.user_id = n.user_id
WHERE n.client_id = %(client_id)s
  AND n.id = ANY(%(notification_ids)s::uuid[])
  AND p.email_enabled
  AND n.kind = ANY(p.kinds)
  AND array_position(ARRAY['low', 'normal', 'high', 'urgent'], n.priority)
      >= array_position(ARRAY['low', 'normal', 'high', 'urgent'], p.min_priority)
ORDER BY n.user_id
ON CONFLICT (notification_id) DO NOTHING
RETURNING id;
