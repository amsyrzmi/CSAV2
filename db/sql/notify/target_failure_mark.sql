-- A target's notification went dead: set the once-per-failure-episode flag, but only if it is not
-- set already, so a run of dead rows raises one bell notice, not one per row. Returns a row only
-- when this call set the flag: the channel, the flag time (clock_timestamp, so a failure right
-- after a clear is a new episode even inside one transaction) and the attempt count of the
-- target's dead rows. A target that is switched off (or retired) is skipped: its dead rows were
-- queued before it was turned off, and the admins asked for that. Named parameters: client_id, target_id.
UPDATE public.staff_notification_targets t
SET failure_notified_at = clock_timestamp()
WHERE t.client_id = %(client_id)s
  AND t.id = %(target_id)s
  AND t.is_enabled
  AND t.failure_notified_at IS NULL
RETURNING t.channel,
          t.failure_notified_at,
          (SELECT max(n.attempts)
             FROM public.staff_notifications n
            WHERE n.client_id = t.client_id AND n.target_id = t.id AND n.status = 'dead'
          ) AS attempts;
