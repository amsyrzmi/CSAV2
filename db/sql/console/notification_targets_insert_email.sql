-- A new email target, saved after its test mail was delivered: enabled and verified now. The
-- unique key (client_id, channel, address) turns a duplicate into no row. Named parameters:
-- client_id, address (lowercased), events, min_priority, quiet_hours (jsonb or NULL), language.
INSERT INTO public.staff_notification_targets
    (client_id, channel, address, events, min_priority, quiet_hours, language, is_enabled,
     verified_at)
VALUES (%(client_id)s, 'email', %(address)s, %(events)s, %(min_priority)s, %(quiet_hours)s,
        %(language)s, true, now())
ON CONFLICT (client_id, channel, address) DO NOTHING
RETURNING id;
