-- Rule 3 of the subscriptions_due job: a paused subscription whose resume date has arrived
-- (compared with today's date on the tenant's calendar, clients.timezone) becomes active again;
-- paused_at and resume_at are cleared. A renewal that lapsed while paused, or a missing one, is
-- set to one billing interval of the current plan after now on the tenant's calendar (the
-- arithmetic subscription_apply.sql uses for a manual resume); a renews_at still in the future
-- is kept.
-- Action 'resumed' with {resume_at, renews_at}. Rows are claimed FOR UPDATE SKIP LOCKED (earliest
-- resume date first, at most %(batch_size)s); version bumps.
WITH tenant AS (
    SELECT timezone FROM public.clients WHERE id = %(client_id)s
), due AS (
    SELECT s.id, s.status AS from_status, s.resume_at
    FROM public.subscriptions s, tenant
    WHERE s.client_id = %(client_id)s
      AND s.status = 'paused'
      AND s.resume_at <= (%(now)s::timestamptz AT TIME ZONE tenant.timezone)::date
    ORDER BY s.resume_at, s.id
    FOR UPDATE OF s SKIP LOCKED
    LIMIT %(batch_size)s
), changed AS (
    UPDATE public.subscriptions s
    SET status = 'active',
        paused_at = NULL,
        resume_at = NULL,
        renews_at = CASE WHEN s.renews_at IS NULL OR s.renews_at < %(now)s::timestamptz THEN
            ((%(now)s::timestamptz AT TIME ZONE tenant.timezone)
             + CASE (SELECT p.billing_interval FROM public.plans p
                     WHERE p.client_id = s.client_id AND p.code = s.plan)
                   WHEN 'year' THEN interval '1 year'
                   ELSE interval '1 month' END)
            AT TIME ZONE tenant.timezone
        ELSE s.renews_at END,
        version = s.version + 1
    FROM due, tenant
    WHERE s.client_id = %(client_id)s AND s.id = due.id
    RETURNING s.id, due.from_status, s.status AS to_status, due.resume_at, s.renews_at
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:subscriptions_due', 'resumed', from_status, to_status,
           jsonb_build_object('resume_at', resume_at, 'renews_at', renews_at)
    FROM changed
    RETURNING id
)
SELECT count(*) AS resumed FROM logged;
