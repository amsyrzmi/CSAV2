SELECT id AS credential_id, access_token
FROM public.channel_credentials
WHERE client_id = %s
  AND channel = 'telegram'
  AND access_token IS NOT NULL
ORDER BY updated_at DESC
LIMIT 1;
