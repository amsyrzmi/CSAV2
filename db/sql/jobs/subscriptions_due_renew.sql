-- Rule 4 of the subscriptions_due job: an active subscription not flagged to cancel whose
-- renews_at has passed renews. renews_at becomes the first step of the cycle after the old one
-- (subscription_next_renewal): the cycle's anchor, coalesce(renewal_anchor, renews_at), plus whole
-- billing intervals of its plan (year or month, month when the plan row is gone) on the tenant's
-- calendar (clients.timezone). So the cycle keeps its day of the month and clamps only in a
-- shorter month: 31 Jan -> 28 Feb -> 31 Mar. The anchor is stored (a NULL one is set to the old
-- renews_at, the row's own cycle from here on); both read the row's values from before the update.
-- Nothing is charged. A subscription far behind catches up one interval per run: the statement
-- touches each row once, so no row renews twice in a run.
-- Action 'renewed' with {previous_renews_at, renews_at}. Rows are claimed FOR UPDATE SKIP LOCKED
-- (oldest first, at most %(batch_size)s); version bumps.
WITH tenant AS (
    SELECT timezone FROM public.clients WHERE id = %(client_id)s
), due AS (
    SELECT s.id, s.status AS from_status, s.renews_at AS previous_renews_at
    FROM public.subscriptions s
    WHERE s.client_id = %(client_id)s
      AND s.status = 'active'
      AND NOT s.cancel_at_period_end
      AND s.renews_at <= %(now)s::timestamptz
    ORDER BY s.renews_at, s.id
    FOR UPDATE OF s SKIP LOCKED
    LIMIT %(batch_size)s
), changed AS (
    UPDATE public.subscriptions s
    SET renews_at = public.subscription_next_renewal(
            coalesce(s.renewal_anchor, s.renews_at), s.renews_at,
            coalesce((SELECT p.billing_interval FROM public.plans p
                      WHERE p.client_id = s.client_id AND p.code = s.plan), 'month'),
            tenant.timezone),
        renewal_anchor = coalesce(s.renewal_anchor, s.renews_at),
        version = s.version + 1
    FROM due, tenant
    WHERE s.client_id = %(client_id)s AND s.id = due.id
    RETURNING s.id, due.from_status, s.status AS to_status, due.previous_renews_at, s.renews_at
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:subscriptions_due', 'renewed', from_status, to_status,
           jsonb_build_object('previous_renews_at', previous_renews_at, 'renews_at', renews_at)
    FROM changed
    RETURNING id
)
SELECT count(*) AS renewed FROM logged;
