-- A delivery, a successful test or an edit: the target works again, so the next dead row raises a
-- new bell notice. Writes nothing when the flag is already clear. Named parameters: client_id,
-- target_id.
UPDATE public.staff_notification_targets
SET failure_notified_at = NULL
WHERE client_id = %(client_id)s
  AND id = %(target_id)s
  AND failure_notified_at IS NOT NULL;
