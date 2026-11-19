-- Mail path: claims one due row whose target is an email target (the Telegram sender's claim.sql
-- skips those). Same returned columns as claim.sql (held_at: set when quiet hours deferred the row).
WITH candidate AS (
    SELECT n.id
    FROM public.staff_notifications AS n
    JOIN public.staff_notification_targets AS t
      ON t.id = n.target_id AND t.client_id = n.client_id
    WHERE n.status = 'pending'
      AND n.next_attempt_at <= now()
      AND t.channel = 'email'
    ORDER BY n.next_attempt_at, n.created_at, n.id
    LIMIT 1
    FOR UPDATE OF n SKIP LOCKED
)
UPDATE public.staff_notifications AS n
SET status = 'delivering',
    locked_at = now(),
    attempts = n.attempts + 1
FROM candidate
WHERE n.id = candidate.id
RETURNING n.id, n.client_id, n.target_id, n.event, n.priority, n.payload, n.attempts, n.held_at;
