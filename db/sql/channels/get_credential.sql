SELECT id, client_id, channel, shop_id, access_token, refresh_token, expires_at, meta
FROM public.channel_credentials
WHERE client_id = %s
  AND channel = %s
  AND shop_id = %s;
