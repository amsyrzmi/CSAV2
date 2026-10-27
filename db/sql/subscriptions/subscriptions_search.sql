-- %(like)s is an already-escaped ILIKE pattern; %(digits)s holds only 0-9 (empty = no phone match).
SELECT s.id, s.contact_id, c.full_name, c.phone, c.email,
       s.plan, p.name AS plan_name, s.status, s.price, s.currency, s.started_at, s.renews_at,
       s.paused_at, s.cancelled_at, s.resume_at, s.pending_plan, s.pending_effective_at,
       s.cancel_at_period_end, s.version
FROM public.subscriptions s
JOIN public.contacts c ON c.client_id = s.client_id AND c.id = s.contact_id
LEFT JOIN public.plans p ON p.client_id = s.client_id AND p.code = s.plan
WHERE s.client_id = %(client_id)s
  AND (%(status)s::text IS NULL OR s.status = %(status)s)
  AND (%(plan)s::text IS NULL OR s.plan = %(plan)s)
  AND (%(like)s::text IS NULL
       OR c.full_name ILIKE %(like)s
       OR c.email ILIKE %(like)s
       OR (%(digits)s <> '' AND regexp_replace(coalesce(c.phone, ''), '\D', '', 'g')
           LIKE '%%' || %(digits)s || '%%'))
  AND (%(before)s::uuid IS NULL OR (s.started_at, s.id) <
       (SELECT b.started_at, b.id FROM public.subscriptions b
         WHERE b.client_id = %(client_id)s AND b.id = %(before)s))
ORDER BY s.started_at DESC, s.id DESC
LIMIT %(limit)s;
