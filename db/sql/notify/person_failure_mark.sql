-- A person's email went dead: set the once-per-failure-episode flag on their saved preferences,
-- but only if it is not set already. A person with email on always has a row; with no row nothing
-- is written and nothing is returned. Returns the flag time only when this call set it. Named
-- parameters: client_id, user_id.
UPDATE public.console_notification_prefs
SET failure_notified_at = clock_timestamp()
WHERE client_id = %(client_id)s
  AND user_id = %(user_id)s
  AND failure_notified_at IS NULL
RETURNING failure_notified_at;
