-- Delete up to batch console notifications older than the cutoff, oldest first, read or not
-- (console_notifications_purge). Named parameters: client_id, cutoff, batch. Returns how many.
WITH doomed AS (
    SELECT n.id
    FROM public.console_notifications n
    WHERE n.client_id = %(client_id)s AND n.created_at < %(cutoff)s
    ORDER BY n.created_at
    LIMIT %(batch)s
    FOR UPDATE SKIP LOCKED
), gone AS (
    DELETE FROM public.console_notifications n
    USING doomed d
    WHERE n.id = d.id AND n.client_id = %(client_id)s
    RETURNING n.id
)
SELECT count(*)::int AS purged FROM gone;
