SELECT id FROM channel_credentials
WHERE client_id = %s AND id = %s AND channel = 'tiktok_shop'
  AND shop_id = %s AND credential_generation = %s
FOR UPDATE;
