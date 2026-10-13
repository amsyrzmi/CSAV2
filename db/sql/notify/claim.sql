WITH candidate AS (
    SELECT n.id
    FROM public.staff_notifications AS n
    WHERE n.status = 'pending'
      AND n.next_attempt_at <= now()
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
RETURNING n.id, n.client_id, n.target_id, n.event, n.priority, n.payload, n.attempts;
