-- Link reviews for staff, newest first: by status (NULL: any) or one review by id. Only ids,
-- names, phones and channel names of the two contacts leave this statement.
SELECT r.id, r.reason, r.status, r.resolution, r.created_at,
       a.id AS a_id, a.full_name AS a_full_name, a.phone AS a_phone,
       ARRAY(SELECT DISTINCT ci.channel FROM public.contact_identities ci
              WHERE ci.client_id = r.client_id AND ci.contact_id = a.id
              ORDER BY ci.channel) AS a_channels,
       b.id AS b_id, b.full_name AS b_full_name, b.phone AS b_phone,
       ARRAY(SELECT DISTINCT ci.channel FROM public.contact_identities ci
              WHERE ci.client_id = r.client_id AND ci.contact_id = b.id
              ORDER BY ci.channel) AS b_channels
FROM public.contact_link_reviews r
JOIN public.contacts a ON a.client_id = r.client_id AND a.id = r.contact_a
JOIN public.contacts b ON b.client_id = r.client_id AND b.id = r.contact_b
WHERE r.client_id = %(client_id)s
  AND (%(review_id)s::uuid IS NULL OR r.id = %(review_id)s::uuid)
  AND (%(status)s::text IS NULL OR r.status = %(status)s::text)
ORDER BY r.created_at DESC, r.id DESC
LIMIT %(limit)s;
