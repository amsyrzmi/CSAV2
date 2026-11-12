-- The phone of the chat's own contact and whether its channel verified it. Only this contact:
-- the id comes from the turn envelope, never from the planner or a typed number.
SELECT phone, phone_verified_at IS NOT NULL AS verified
FROM public.contacts
WHERE client_id = %(client_id)s AND id = %(contact_id)s;
