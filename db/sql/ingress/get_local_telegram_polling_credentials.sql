SELECT
  c.id AS credential_id,
  c.access_token AS encrypted_bot_token,
  c.meta,
  e.public_id
FROM public.clients AS client
JOIN public.channel_credentials AS c
  ON c.client_id = client.id
 AND c.shop_id = client.slug
JOIN public.webhook_endpoints AS e
  ON e.client_id = c.client_id
 AND e.credential_id = c.id
WHERE client.slug = 'local-dev-telegram'
  AND c.channel = 'telegram'
  AND e.is_enabled = true
LIMIT 1;
