-- First-connect fence: the exact pending store, still without a shop, at one generation.
SELECT id FROM channel_credentials
WHERE client_id = %s AND id = %s AND channel = 'tiktok_shop'
  AND shop_id IS NULL AND disconnected_at IS NULL AND credential_generation = %s
FOR UPDATE;
