-- renews_at defaults to one billing interval after started_at when the caller gives none.
WITH plan AS (
    SELECT code, price, currency, billing_interval
    FROM public.plans
    WHERE client_id = %(client_id)s AND code = %(plan_code)s
), start AS (
    SELECT coalesce(%(started_at)s::timestamptz, now()) AS at
)
INSERT INTO public.subscriptions
    (client_id, contact_id, plan, status, price, currency, started_at, renews_at)
SELECT %(client_id)s, %(contact_id)s, plan.code, 'active', plan.price, plan.currency, start.at,
       coalesce(%(renews_at)s::timestamptz,
                start.at + CASE plan.billing_interval WHEN 'year' THEN interval '1 year'
                                                      ELSE interval '1 month' END)
FROM plan, start
RETURNING id;
