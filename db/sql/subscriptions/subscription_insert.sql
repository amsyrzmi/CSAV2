-- Needs an active plan (no plan row -> no result row). A default renews_at is one billing
-- interval after started_at, computed on the tenant's local calendar (clients.timezone) so a
-- month end clamps the way the customer sees it (subscription_next_renewal, the arithmetic the
-- renewal job uses). renewal_anchor, the instant later renewals are counted from so they keep the
-- cycle's day of the month, is the given renews_at, else started_at. renews_at_ok is false (and
-- nothing is inserted) when the renewal would not be after the start.
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
               public.subscription_next_renewal(
                   start.at, start.at, plan.billing_interval, tenant.timezone)
           ) AS renews_at,
           coalesce(%(renews_at)s::timestamptz, start.at) AS anchor
    FROM plan, tenant, start
), inserted AS (
    INSERT INTO public.subscriptions
        (client_id, contact_id, plan, status, price, currency, started_at, renews_at, renewal_anchor)
    SELECT %(client_id)s, %(contact_id)s, code, 'active', price, currency, started_at, renews_at, anchor
    FROM calc
    WHERE renews_at > started_at
    RETURNING id
)
SELECT (SELECT id FROM inserted) AS id, calc.renews_at > calc.started_at AS renews_at_ok
FROM calc;
