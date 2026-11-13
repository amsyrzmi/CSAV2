-- The channels each contact of a link already has an identity on.
SELECT contact_id, channel
FROM public.contact_identities
WHERE client_id = %(client_id)s AND contact_id = ANY (%(ids)s);
