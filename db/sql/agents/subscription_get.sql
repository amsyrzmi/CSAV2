-- One subscription of the chat's own contact: an id of anyone else's matches nothing.
SELECT s.id, s.plan, p.name AS plan_name, p.billing_interval, s.status, s.price, s.currency,
       s.renews_at, s.cancel_at_period_end, s.resume_at, s.pending_plan, pp.name AS pending_plan_name,
       s.pending_effective_at
FROM public.subscriptions s
LEFT JOIN public.plans p ON p.client_id = s.client_id AND p.code = s.plan
LEFT JOIN public.plans pp ON pp.client_id = s.client_id AND pp.code = s.pending_plan
WHERE s.client_id = %(client_id)s AND s.contact_id = %(contact_id)s AND s.id = %(subscription_id)s;
