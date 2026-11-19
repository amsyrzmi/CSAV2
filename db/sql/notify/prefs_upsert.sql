-- Save the person's notification preferences (last write wins). A save also clears the
-- once-per-failure-episode flag, so the next dead email raises a new bell. Named parameters:
-- client_id, user_id, email_enabled, kinds, min_priority. Returns the saved row.
INSERT INTO public.console_notification_prefs
    (client_id, user_id, email_enabled, kinds, min_priority, failure_notified_at, updated_at)
VALUES (%(client_id)s, %(user_id)s, %(email_enabled)s, %(kinds)s::text[], %(min_priority)s,
        NULL, now())
ON CONFLICT (client_id, user_id) DO UPDATE
   SET email_enabled = EXCLUDED.email_enabled,
       kinds = EXCLUDED.kinds,
       min_priority = EXCLUDED.min_priority,
       failure_notified_at = NULL,
       updated_at = now()
RETURNING email_enabled, kinds, min_priority;
