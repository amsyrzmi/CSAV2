-- Rule 5 of the subscriptions_due job: a pending (approved, never started or paid) subscription
-- older than the company's subscription_settings.pending_expiry_days (no row means 14; 0 means
-- never expire) is cancelled, effective now. Age counts from started_at, the approval time of a
-- pending row. Active and paused rows are never touched. The sign-up request that points at the
-- subscription is marked expired in the same statement, only while it is still approved. Rows are
-- claimed FOR UPDATE SKIP LOCKED (oldest first, at most %(batch_size)s), version bumps, and each
-- row gets one 'pending_expired' timeline event. Nothing is charged and nobody is messaged.
-- %(now)s is the job clock, so a test clock and production use the same statement.
WITH setting AS (
    SELECT coalesce(
        (SELECT pending_expiry_days FROM public.subscription_settings
         WHERE client_id = %(client_id)s),
        14) AS days
), due AS (
    SELECT s.id, s.status AS from_status
    FROM public.subscriptions s, setting
    WHERE s.client_id = %(client_id)s
      AND s.status = 'pending'
      AND setting.days > 0
      AND s.started_at < %(now)s::timestamptz - make_interval(days => setting.days)
    ORDER BY s.started_at, s.id
    FOR UPDATE OF s SKIP LOCKED
    LIMIT %(batch_size)s
), changed AS (
    UPDATE public.subscriptions s
    SET status = 'cancelled', cancelled_at = %(now)s::timestamptz, version = s.version + 1
    FROM due
    WHERE s.client_id = %(client_id)s AND s.id = due.id
    RETURNING s.id, due.from_status, s.status AS to_status
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:subscriptions_due', 'pending_expired',
           from_status, to_status, '{}'::jsonb
    FROM changed
    RETURNING id
), signups AS (
    UPDATE public.signup_requests r
    SET status = 'expired'
    FROM changed
    WHERE r.client_id = %(client_id)s AND r.subscription_id = changed.id AND r.status = 'approved'
    RETURNING r.id
)
SELECT count(*) AS pending_expired FROM logged;
