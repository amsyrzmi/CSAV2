-- One console notification per recipient who may hold one in this company (an enabled member:
-- console_notification_members, 0624; anyone else is skipped, not refused). A replay with the same
-- (company, person, dedupe_key) writes nothing. Returns one row per notification written.
INSERT INTO public.console_notifications
    (client_id, user_id, kind, priority, message_key, params, link, dedupe_key)
SELECT %(client_id)s, m.user_id, %(kind)s, %(priority)s, %(message_key)s, %(params)s, %(link)s,
       %(dedupe_key)s
FROM public.console_notification_members(%(client_id)s, %(recipients)s::uuid[]) AS m (user_id)
ORDER BY m.user_id
ON CONFLICT (client_id, user_id, dedupe_key) DO NOTHING
RETURNING user_id;
