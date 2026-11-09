-- One customer's subscriptions, newest first. The client filter is explicit beside RLS, so a
-- customer id from another tenant matches nothing.
SELECT s.id, s.plan, p.name AS plan_name, s.status, s.price, s.currency, s.renews_at
FROM public.subscriptions s
LEFT JOIN public.plans p ON p.client_id = s.client_id AND p.code = s.plan
WHERE s.client_id = %(client_id)s AND s.contact_id = %(customer_id)s
ORDER BY s.started_at DESC, s.id DESC
LIMIT %(limit)s;
