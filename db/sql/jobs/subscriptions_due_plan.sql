-- Rule 2 of the subscriptions_due job: a pending plan change whose effective time has come.
-- The plan's CURRENT price and currency apply at the effective date (not the price quoted when
-- the change was scheduled), and the pending fields are cleared: action 'plan_changed' with
-- {old_plan, new_plan, old_price, new_price}. When the pending plan is archived or gone, or the
-- subscription is already cancelled, nothing is applied and the pending fields are only cleared:
-- action 'pending_change_skipped' with {plan}. Status is unchanged either way. Rows are claimed
-- FOR UPDATE SKIP LOCKED (earliest effective time first, at most %(batch_size)s); version bumps.
-- A change that applies to a plan of another billing interval (a missing plan row counting as
-- monthly) resets renewal_anchor to the current renews_at, so the new interval is counted from the
-- end of the current period (NULL when there is none); the same interval, and a skipped change,
-- leave the anchor alone.
WITH due AS (
    SELECT s.id, s.status, s.plan AS old_plan, s.price AS old_price, s.pending_plan,
           p.code IS NOT NULL AND s.status <> 'cancelled' AS applies,
           p.price AS new_price, p.currency AS new_currency,
           coalesce(p.billing_interval, 'month') <> coalesce(
               (SELECT cp.billing_interval FROM public.plans cp
                WHERE cp.client_id = s.client_id AND cp.code = s.plan), 'month') AS interval_changes
    FROM public.subscriptions s
    LEFT JOIN public.plans p
        ON p.client_id = s.client_id AND p.code = s.pending_plan AND p.is_active
    WHERE s.client_id = %(client_id)s
      AND s.pending_plan IS NOT NULL
      AND s.pending_effective_at <= %(now)s::timestamptz
    ORDER BY s.pending_effective_at, s.id
    FOR UPDATE OF s SKIP LOCKED
    LIMIT %(batch_size)s
), changed AS (
    UPDATE public.subscriptions s
    SET plan = CASE WHEN due.applies THEN due.pending_plan ELSE s.plan END,
        price = CASE WHEN due.applies THEN due.new_price ELSE s.price END,
        currency = CASE WHEN due.applies THEN due.new_currency ELSE s.currency END,
        renewal_anchor = CASE WHEN due.applies AND due.interval_changes THEN s.renews_at
                              ELSE s.renewal_anchor END,
        pending_plan = NULL,
        pending_effective_at = NULL,
        version = s.version + 1
    FROM due
    WHERE s.client_id = %(client_id)s AND s.id = due.id
    RETURNING s.id, s.status, due.applies, due.old_plan, due.old_price, due.pending_plan,
              due.new_price
), logged AS (
    INSERT INTO public.subscription_events
        (client_id, subscription_id, actor, action, from_status, to_status, payload)
    SELECT %(client_id)s, id, 'system:subscriptions_due',
           CASE WHEN applies THEN 'plan_changed' ELSE 'pending_change_skipped' END,
           status, status,
           CASE WHEN applies THEN
               jsonb_build_object('old_plan', old_plan, 'new_plan', pending_plan,
                                  'old_price', old_price, 'new_price', new_price)
           ELSE jsonb_build_object('plan', pending_plan) END
    FROM changed
    RETURNING action
)
SELECT count(*) FILTER (WHERE action = 'plan_changed') AS plan_changed,
       count(*) FILTER (WHERE action = 'pending_change_skipped') AS pending_skipped
FROM logged;
