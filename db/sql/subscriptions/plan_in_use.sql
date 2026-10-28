-- True while an active or paused subscription is on the plan or is scheduled to move to it.
-- Subscriptions snapshot price and currency but read the plan's billing_interval, so the
-- interval of a plan in use must not change.
SELECT EXISTS (
    SELECT 1
    FROM public.subscriptions
    WHERE client_id = %(client_id)s
      AND status IN ('active', 'paused')
      AND (plan = %(code)s OR pending_plan = %(code)s)
) AS in_use;
