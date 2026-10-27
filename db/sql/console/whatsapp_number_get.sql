SELECT c.id AS credential_id, c.shop_id, c.meta, c.updated_at,
       (c.access_token IS NOT NULL) AS has_token, coalesce(e.is_enabled, false) AS is_enabled
FROM public.channel_credentials c
LEFT JOIN public.webhook_endpoints e ON e.credential_id = c.id
WHERE c.client_id = %s AND c.channel = 'whatsapp'
ORDER BY c.updated_at DESC
LIMIT 1;
