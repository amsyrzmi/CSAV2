INSERT INTO public.contact_identities (client_id, channel, external_id, contact_id)
VALUES (%s, 'telegram', %s, %s)
ON CONFLICT (client_id, channel, external_id) DO NOTHING
RETURNING contact_id
