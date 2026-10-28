-- Applies one subscription_action to a row the caller locked (subscription_lock.sql) at
-- %(version)s; "now" is the transaction's now(). immediate_plan / scheduled_plan say whether a
-- change_plan takes effect now (plan, price and currency replaced, pending cleared) or later
-- (pending_plan and pending_effective_at set).
-- renewal_anchor is the instant renewals are counted from (subscription_next_renewal), so they keep
-- the cycle's day of the month:
--   extend_renewal         anchor = the new renews_at;
--   resume, lapsed or no   anchor = now(), and renews_at is one billing interval of the current plan
--     renewal date           after it on the tenant's calendar (clients.timezone); a resume that is
--                            not lapsed changes neither;
--   immediate change_plan  anchor = the current renews_at (NULL when there is none; renews_at is
--     to another interval    unchanged) when the target plan's billing interval differs from the
--                            current plan's, a missing plan row counting as monthly; the same
--                            interval, and every other action, leave the anchor alone.
WITH tenant AS (
    SELECT timezone FROM public.clients WHERE id = %(client_id)s
), current_plan AS (
    SELECT coalesce((SELECT p.billing_interval FROM public.plans p
                      WHERE p.client_id = %(client_id)s AND p.code = s.plan), 'month') AS billing_interval
    FROM public.subscriptions s
    WHERE s.client_id = %(client_id)s AND s.id = %(subscription_id)s
)
UPDATE public.subscriptions s
SET status = CASE %(action)s
        WHEN 'pause' THEN 'paused'
        WHEN 'resume' THEN 'active'
        WHEN 'cancel_now' THEN 'cancelled'
        ELSE s.status END,
    paused_at = CASE %(action)s
        WHEN 'pause' THEN now()
        WHEN 'resume' THEN NULL
        ELSE s.paused_at END,
    resume_at = CASE %(action)s
        WHEN 'pause' THEN %(resume_at)s::date
        WHEN 'resume' THEN NULL
        WHEN 'cancel_now' THEN NULL
        ELSE s.resume_at END,
    renews_at = CASE
        WHEN %(action)s = 'extend_renewal' THEN %(renews_at)s::timestamptz
        WHEN %(action)s = 'resume' AND (s.renews_at IS NULL OR s.renews_at < now()) THEN
            public.subscription_next_renewal(
                now(), now(), current_plan.billing_interval, tenant.timezone)
        ELSE s.renews_at END,
    renewal_anchor = CASE
        WHEN %(action)s = 'extend_renewal' THEN %(renews_at)s::timestamptz
        WHEN %(action)s = 'resume' AND (s.renews_at IS NULL OR s.renews_at < now()) THEN now()
        WHEN %(immediate_plan)s
             AND coalesce(%(target_interval)s, 'month') <> current_plan.billing_interval THEN s.renews_at
        ELSE s.renewal_anchor END,
    plan = CASE WHEN %(immediate_plan)s THEN %(plan_code)s ELSE s.plan END,
    price = CASE WHEN %(immediate_plan)s THEN %(price)s::numeric ELSE s.price END,
    currency = CASE WHEN %(immediate_plan)s THEN %(currency)s ELSE s.currency END,
    pending_plan = CASE
        WHEN %(scheduled_plan)s THEN %(plan_code)s
        WHEN %(immediate_plan)s OR %(action)s = 'cancel_now' THEN NULL
        ELSE s.pending_plan END,
    pending_effective_at = CASE
        WHEN %(scheduled_plan)s THEN %(effective_at)s::timestamptz
        WHEN %(immediate_plan)s OR %(action)s = 'cancel_now' THEN NULL
        ELSE s.pending_effective_at END,
    cancelled_at = CASE %(action)s WHEN 'cancel_now' THEN now() ELSE s.cancelled_at END,
    cancel_at_period_end = CASE %(action)s
        WHEN 'cancel_at_period_end' THEN true
        WHEN 'cancel_now' THEN false
        ELSE s.cancel_at_period_end END,
    version = s.version + 1
FROM tenant, current_plan
WHERE s.client_id = %(client_id)s AND s.id = %(subscription_id)s AND s.version = %(version)s
RETURNING s.status, s.version, s.renews_at;
