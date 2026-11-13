-- The phone of the chat's own contact, whether its channel verified it, and whether it has an
-- address to be mailed at (its own email, or one the customer gave in a chat). Only this contact:
-- the id comes from the turn envelope, never from the planner or a typed number.
SELECT phone, phone_verified_at IS NOT NULL AS verified,
       (nullif(btrim(email), '') IS NOT NULL
        OR nullif(btrim(customer_given_email), '') IS NOT NULL) AS has_email
FROM public.contacts
WHERE client_id = %(client_id)s AND id = %(contact_id)s;
