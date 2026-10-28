-- Applies one subscription_action to a row the caller locked (subscription_lock.sql) at
-- %(version)s; "now" is the transaction's now(). immediate_plan / scheduled_plan say whether a
-- change_plan takes effect now (plan, price and currency replaced, pending cleared) or later
-- (pending_plan and pending_effective_at set). A resume whose renewal has lapsed, or that has
-- no renewal date at all, renews one billing interval of the current plan after now on the
-- tenant's calendar (clients.timezone), the arithmetic subscription_insert.sql uses for a default
-- renewal.
WITH tenant AS (
    SELECT timezone FROM public.clients WHERE id = %(client_id)s
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
            ((now() AT TIME ZONE tenant.timezone)
             + CASE (SELECT p.billing_interval FROM public.plans p
                     WHERE p.client_id = s.client_id AND p.code = s.plan)
                   WHEN 'year' THEN interval '1 year'
                   ELSE interval '1 month' END)
            AT TIME ZONE tenant.timezone
        ELSE s.renews_at END,
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
FROM tenant
WHERE s.client_id = %(client_id)s AND s.id = %(subscription_id)s AND s.version = %(version)s
RETURNING s.status, s.version, s.renews_at;
