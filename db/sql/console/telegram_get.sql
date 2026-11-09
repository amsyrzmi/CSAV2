SELECT c.id AS credential_id, c.shop_id, c.meta, c.access_token, c.updated_at,
       (c.access_token IS NOT NULL) AS has_token,
       e.id AS endpoint_id, e.public_id, coalesce(e.is_enabled, false) AS is_enabled
FROM public.channel_credentials c
LEFT JOIN public.webhook_endpoints e ON e.credential_id = c.id
WHERE c.client_id = %s AND c.channel = 'telegram' AND c.disconnected_at IS NULL
LIMIT 1;
