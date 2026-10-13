WITH known AS MATERIALIZED (
    SELECT c.id AS contact_id, c.full_name
    FROM public.contact_identities AS i
    JOIN public.contacts AS c ON c.id = i.contact_id
    WHERE i.client_id = %(client_id)s
      AND i.channel = 'telegram'
      AND i.external_id = %(sender_id)s
      AND c.client_id = %(client_id)s
      AND c.telegram_id = %(telegram_id)s
), created AS (
    INSERT INTO public.contacts (client_id, telegram_id, full_name)
    SELECT %(client_id)s, %(telegram_id)s, %(display_name)s
    WHERE NOT EXISTS (SELECT 1 FROM known)
    ON CONFLICT (client_id, telegram_id) DO NOTHING
    RETURNING id, full_name
), identity AS (
    INSERT INTO public.contact_identities (client_id, channel, external_id, contact_id)
    SELECT %(client_id)s, 'telegram', %(sender_id)s, id FROM created
    ON CONFLICT (client_id, channel, external_id) DO NOTHING
    RETURNING contact_id
)
SELECT contact_id, full_name, false AS first_contact FROM known
UNION ALL
SELECT c.id AS contact_id, c.full_name, true AS first_contact
FROM created AS c
JOIN identity AS i ON i.contact_id = c.id
