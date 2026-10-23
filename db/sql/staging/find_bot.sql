SELECT c.id, c.client_id, c.access_token, c.meta, e.public_id, e.is_enabled
FROM public.channel_credentials c
LEFT JOIN public.webhook_endpoints e ON e.credential_id = c.id
WHERE c.channel = 'telegram' AND c.shop_id = %s
FOR UPDATE OF c;
