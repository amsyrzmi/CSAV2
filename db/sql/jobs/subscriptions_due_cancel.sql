-- Rule 1 of the subscriptions_due job: an active subscription flagged cancel_at_period_end whose
-- period has ended becomes cancelled, effective at the period end (cancelled_at = renews_at).
-- Rows are claimed FOR UPDATE SKIP LOCKED (oldest period end first, at most %(batch_size)s), the
-- change bumps version, and each row gets one 'cancelled_at_period_end' timeline event. Nothing is
-- charged. %(now)s is the job clock, so a test clock and production use the same statement.
WITH due AS (
    SELECT id, status AS from_status
    FROM public.subscriptions
    WHERE client_id = %(client_id)s
      AND status = 'active'
      AND cancel_at_period_end
      AND renews_at <= %(now)s::timestamptz
    ORDER BY renews_at, id
    FOR UPDATE SKIP LOCKED
    LIMIT %(batch_size)s
), changed AS (
    UPDATE public.subscriptions s
    SET status = 'cancelled', cancelled_at = s.renews_at, version = s.version + 1
    FROM due
    WHERE s.client_id = %(client_id)s AND s.id = due.id
    RETURNING s.id, due.from_status, s.status AS to_status
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:subscriptions_due', 'cancelled_at_period_end',
           from_status, to_status, '{}'::jsonb
    FROM changed
    RETURNING id
)
SELECT count(*) AS cancelled FROM logged;
