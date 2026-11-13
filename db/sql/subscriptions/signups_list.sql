-- Sign-up requests, newest first; %(status)s is 'pending' (waiting for staff) or 'decided'
-- (approved, declined or expired). Same columns as signup_get.sql.
SELECT r.id, r.contact_id, c.full_name AS contact_full_name, c.phone AS contact_phone,
       c.email AS contact_email, r.plan_code, p.name AS plan_name, r.full_name, r.phone,
       r.email,
       (c.phone_verified_at IS NOT NULL
        AND coalesce(c.phone_digits = public.my_phone_digits(r.phone), false)) AS phone_verified,
       r.status, r.decided_via, r.decline_reason, r.subscription_id, r.created_at, r.decided_at,
       (SELECT i.channel FROM public.contact_identities i
         WHERE i.client_id = r.client_id AND i.contact_id = r.contact_id
         ORDER BY i.created_at DESC, i.channel LIMIT 1) AS source_channel,
       (SELECT CASE WHEN v.status = 'void' THEN NULL ELSE v.reference END
          FROM public.invoices v
         WHERE v.client_id = r.client_id AND v.subscription_id = r.subscription_id
           AND v.status <> 'draft'
         ORDER BY v.period_start IS NOT NULL, v.issued_at, v.id LIMIT 1) AS invoice_reference
FROM public.signup_requests r
JOIN public.contacts c ON c.client_id = r.client_id AND c.id = r.contact_id
LEFT JOIN public.plans p ON p.client_id = r.client_id AND p.code = r.plan_code
WHERE r.client_id = %(client_id)s
  AND (CASE WHEN %(status)s::text = 'pending' THEN r.status = 'pending'
            ELSE r.status <> 'pending' END)
  AND (%(before)s::uuid IS NULL OR (r.created_at, r.id) <
       (SELECT b.created_at, b.id FROM public.signup_requests b
         WHERE b.client_id = %(client_id)s AND b.id = %(before)s))
ORDER BY r.created_at DESC, r.id DESC
LIMIT %(limit)s;
