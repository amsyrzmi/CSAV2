-- Replace a target's settings, but only from the version the caller read; bumps the version.
-- No row means the version was stale (the caller has already found the target). The address,
-- channel and verification are not editable here. Named parameters: client_id, target_id, version,
-- events, min_priority, quiet_hours (jsonb or NULL), language, is_enabled.
UPDATE public.staff_notification_targets
SET events = %(events)s,
    min_priority = %(min_priority)s,
    quiet_hours = %(quiet_hours)s,
    language = %(language)s,
    is_enabled = %(is_enabled)s,
    version = version + 1
WHERE client_id = %(client_id)s
  AND id = %(target_id)s
  AND version = %(version)s
RETURNING id;
