-- One sign-up request as the staff list shows it. phone_verified: the contact's phone is
-- verified (0581) and is the number typed on the request; a different typed number is not.
-- source_channel: the newest channel identity of the contact.
SELECT r.id, r.contact_id, c.full_name AS contact_full_name, c.phone AS contact_phone,
       c.email AS contact_email, r.plan_code, p.name AS plan_name, r.full_name, r.phone,
       (c.phone_verified_at IS NOT NULL
        AND coalesce(c.phone_digits = public.my_phone_digits(r.phone), false)) AS phone_verified,
       r.status, r.decided_via, r.decline_reason, r.subscription_id, r.created_at, r.decided_at,
       (SELECT i.channel FROM public.contact_identities i
         WHERE i.client_id = r.client_id AND i.contact_id = r.contact_id
         ORDER BY i.created_at DESC, i.channel LIMIT 1) AS source_channel
FROM public.signup_requests r
JOIN public.contacts c ON c.client_id = r.client_id AND c.id = r.contact_id
LEFT JOIN public.plans p ON p.client_id = r.client_id AND p.code = r.plan_code
WHERE r.client_id = %(client_id)s AND r.id = %(request_id)s;
