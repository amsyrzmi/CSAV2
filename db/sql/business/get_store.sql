SELECT id, channel AS platform, shop_id, expires_at, updated_at
FROM public.channel_credentials
WHERE client_id = %s AND id = %s
  AND channel IN ('tiktok_shop', 'shopee', 'lazada')
  AND (%s::text IS NULL OR channel = %s)
  AND (%s::text IS NULL OR shop_id = %s);
