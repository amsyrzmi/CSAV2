-- A delivered email: the person's mail works again, so the next dead email raises a new bell
-- notice. Writes nothing when the flag is already clear (or there is no row). Named parameters:
-- client_id, user_id.
UPDATE public.console_notification_prefs
SET failure_notified_at = NULL
WHERE client_id = %(client_id)s
  AND user_id = %(user_id)s
  AND failure_notified_at IS NOT NULL;
