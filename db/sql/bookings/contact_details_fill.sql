-- A customer's name, email and the address they gave in a chat, as they are now, after filling only
-- what is empty with what the customer just said. A detail already on file is never replaced.
-- The address a customer types is a claim nobody has checked: it goes into customer_given_email,
-- never into email, which identity (the walk-in match, the Inbox's "known") trusts. It is filled
-- only when the contact has no email of its own and no given address yet.
WITH filled AS (
    UPDATE public.contacts c
    SET full_name = CASE
            WHEN %(name)s::text IS NOT NULL AND nullif(btrim(c.full_name), '') IS NULL
                THEN %(name)s::text ELSE c.full_name END,
        customer_given_email = CASE
            WHEN %(email)s::text IS NOT NULL AND nullif(btrim(c.email), '') IS NULL
                 AND nullif(btrim(c.customer_given_email), '') IS NULL
                THEN %(email)s::text ELSE c.customer_given_email END
    WHERE c.client_id = %(client_id)s AND c.id = %(id)s
      AND ((%(name)s::text IS NOT NULL AND nullif(btrim(c.full_name), '') IS NULL)
        OR (%(email)s::text IS NOT NULL AND nullif(btrim(c.email), '') IS NULL
            AND nullif(btrim(c.customer_given_email), '') IS NULL))
    RETURNING c.full_name, c.email, c.customer_given_email
)
SELECT full_name, email, customer_given_email FROM filled
UNION ALL
SELECT full_name, email, customer_given_email FROM public.contacts
WHERE client_id = %(client_id)s AND id = %(id)s AND NOT EXISTS (SELECT 1 FROM filled)
LIMIT 1;
