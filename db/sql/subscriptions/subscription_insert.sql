-- Needs an active plan (no plan row -> no result row). A default renews_at is one billing
-- interval after started_at, computed on the tenant's local calendar (clients.timezone) so a
-- month end clamps the way the customer sees it. renews_at_ok is false (and nothing is
-- inserted) when the renewal would not be after the start.
WITH plan AS (
    SELECT code, price, currency, billing_interval
    FROM public.plans
    WHERE client_id = %(client_id)s AND code = %(plan_code)s AND is_active
), tenant AS (
    SELECT timezone FROM public.clients WHERE id = %(client_id)s
), start AS (
    SELECT coalesce(%(started_at)s::timestamptz, now()) AS at
), calc AS (
    SELECT plan.code, plan.price, plan.currency, start.at AS started_at,
           coalesce(
               %(renews_at)s::timestamptz,
               ((start.at AT TIME ZONE tenant.timezone)
                + CASE plan.billing_interval WHEN 'year' THEN interval '1 year'
                                             ELSE interval '1 month' END)
               AT TIME ZONE tenant.timezone
           ) AS renews_at
    FROM plan, tenant, start
), inserted AS (
    INSERT INTO public.subscriptions
        (client_id, contact_id, plan, status, price, currency, started_at, renews_at)
    SELECT %(client_id)s, %(contact_id)s, code, 'active', price, currency, started_at, renews_at
    FROM calc
    WHERE renews_at > started_at
    RETURNING id
)
SELECT (SELECT id FROM inserted) AS id, calc.renews_at > calc.started_at AS renews_at_ok
FROM calc;
