-- The latest dead notice of each target of the company that still counts as failing: the target's
-- once-per-episode flag (failure_notified_at) is set, which a delivery, a successful test and an
-- edit all clear, and no later notice of the target was delivered. "at" is the dead notice's
-- created_at (when the notice was raised; the table has no time of death, and next_attempt_at is
-- pushed forward by the backoff). Named parameters: client_id.
SELECT DISTINCT ON (n.target_id) n.target_id, n.created_at AS at, n.attempts
FROM public.staff_notifications n
JOIN public.staff_notification_targets t ON t.id = n.target_id AND t.client_id = n.client_id
WHERE n.client_id = %(client_id)s
  AND n.status = 'dead'
  AND t.failure_notified_at IS NOT NULL
  AND NOT EXISTS (SELECT 1
                    FROM public.staff_notifications d
                   WHERE d.client_id = n.client_id AND d.target_id = n.target_id
                     AND d.status = 'delivered' AND d.delivered_at > n.created_at)
ORDER BY n.target_id, n.created_at DESC, n.id;
