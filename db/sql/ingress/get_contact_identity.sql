SELECT contact_id
FROM public.contact_identities
WHERE client_id = %s
  AND channel = 'telegram'
  AND external_id = %s
