-- A customer's name and email as they are now, after filling only the ones that are empty with
-- what the customer just said. A detail already on file is never replaced, and an address another
-- contact of the company already has is ignored (it must not say that one exists).
WITH filled AS (
    UPDATE public.contacts c
    SET full_name = CASE
            WHEN %(name)s::text IS NOT NULL AND nullif(btrim(c.full_name), '') IS NULL
                THEN %(name)s::text ELSE c.full_name END,
        email = CASE
            WHEN %(email)s::text IS NOT NULL AND nullif(btrim(c.email), '') IS NULL
                 AND NOT EXISTS (SELECT 1 FROM public.contacts o
                                 WHERE o.client_id = c.client_id AND o.id <> c.id
                                   AND lower(o.email) = lower(%(email)s::text))
                THEN %(email)s::text ELSE c.email END
    WHERE c.client_id = %(client_id)s AND c.id = %(id)s
      AND ((%(name)s::text IS NOT NULL AND nullif(btrim(c.full_name), '') IS NULL)
        OR (%(email)s::text IS NOT NULL AND nullif(btrim(c.email), '') IS NULL))
    RETURNING c.full_name, c.email
)
SELECT full_name, email FROM filled
UNION ALL
SELECT full_name, email FROM public.contacts
WHERE client_id = %(client_id)s AND id = %(id)s AND NOT EXISTS (SELECT 1 FROM filled)
LIMIT 1;
