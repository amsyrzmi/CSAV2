WITH known AS MATERIALIZED (
    SELECT c.id AS contact_id, c.full_name
    FROM public.contact_identities AS i
    JOIN public.contacts AS c ON c.id = i.contact_id AND c.client_id = i.client_id
    WHERE i.client_id = %(client_id)s
      AND i.channel = 'whatsapp'
      AND i.external_id = %(wa_id)s
), phone_matches AS MATERIALIZED (
    SELECT c.id, c.full_name
    FROM public.contacts AS c
    WHERE c.client_id = %(client_id)s
      AND regexp_replace(c.phone, '\D', '', 'g') = %(wa_id)s
), linked AS MATERIALIZED (
    SELECT id AS contact_id, full_name
    FROM phone_matches
    WHERE NOT EXISTS (SELECT 1 FROM known)
      AND (SELECT count(*) FROM phone_matches) = 1
), created AS (
    INSERT INTO public.contacts (client_id, phone, full_name)
    SELECT %(client_id)s, '+' || %(wa_id)s, %(display_name)s
    WHERE NOT EXISTS (SELECT 1 FROM known)
      AND NOT EXISTS (SELECT 1 FROM linked)
    RETURNING id, full_name
), identity AS (
    INSERT INTO public.contact_identities (client_id, channel, external_id, contact_id)
    SELECT %(client_id)s, 'whatsapp', %(wa_id)s, contact_id FROM linked
    UNION ALL
    SELECT %(client_id)s, 'whatsapp', %(wa_id)s, id FROM created
    ON CONFLICT (client_id, channel, external_id) DO NOTHING
    RETURNING contact_id
)
SELECT contact_id, full_name, false AS first_contact FROM known
UNION ALL
SELECT contact_id, full_name, false AS first_contact FROM linked
UNION ALL
SELECT c.id AS contact_id, c.full_name, true AS first_contact
FROM created AS c
JOIN identity AS i ON i.contact_id = c.id
