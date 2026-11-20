-- Delete up to batch email outbox rows created before the cutoff, oldest first, whatever their
-- status (console_notifications_purge, with the notifications they point at). Named parameters:
-- client_id, cutoff, batch. Returns how many.
WITH doomed AS (
    SELECT o.id
    FROM public.console_email_outbox o
    WHERE o.client_id = %(client_id)s AND o.created_at < %(cutoff)s
    ORDER BY o.created_at
    LIMIT %(batch)s
    FOR UPDATE SKIP LOCKED
), gone AS (
    DELETE FROM public.console_email_outbox o
    USING doomed d
    WHERE o.id = d.id AND o.client_id = %(client_id)s
    RETURNING o.id
)
SELECT count(*)::int AS purged FROM gone;
