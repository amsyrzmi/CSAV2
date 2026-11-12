-- The contact a merged one points at, or NULL when it is live.
SELECT merged_into FROM public.contacts WHERE client_id = %(client_id)s AND id = %(contact_id)s;
