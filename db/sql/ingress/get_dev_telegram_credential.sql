SELECT c.id AS credential_id, e.id AS endpoint_id, e.public_id
FROM public.channel_credentials AS c
JOIN public.webhook_endpoints AS e
  ON e.credential_id = c.id
 AND e.client_id = c.client_id
WHERE c.client_id = %s
  AND c.channel = 'telegram'
  AND c.shop_id = 'local-dev-telegram'
LIMIT 1
