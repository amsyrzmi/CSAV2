-- The pending subscription an approval creates: price and currency are the plan's now, started_at
-- is the approval time (the column is NOT NULL), renews_at and renewal_anchor stay NULL until
-- staff activate it. No row when the plan is archived or closed to sign-ups.
INSERT INTO public.subscriptions
    (client_id, contact_id, plan, status, price, currency, started_at)
SELECT %(client_id)s, %(contact_id)s, p.code, 'pending', p.price, p.currency, now()
FROM public.plans p
WHERE p.client_id = %(client_id)s AND p.code = %(plan_code)s AND p.is_active AND p.is_signup_open
RETURNING id;
