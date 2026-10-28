SELECT s.id, s.contact_id, c.full_name, c.phone, c.email,
       s.plan, p.name AS plan_name, s.status, s.price, s.currency, s.started_at, s.renews_at,
       s.paused_at, s.cancelled_at, s.resume_at, s.pending_plan, s.pending_effective_at,
       s.cancel_at_period_end, s.version
FROM public.subscriptions s
JOIN public.contacts c ON c.client_id = s.client_id AND c.id = s.contact_id
LEFT JOIN public.plans p ON p.client_id = s.client_id AND p.code = s.plan
WHERE s.client_id = %(client_id)s AND s.id = %(subscription_id)s;
