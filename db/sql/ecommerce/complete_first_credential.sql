-- First connect: bind the single verified shop to the exact pending store and store its tokens.
-- One UPDATE, so the 0320 fence bumps credential_generation exactly once. The global active-shop
-- index (0491) and the baseline UNIQUE (client_id, channel, shop_id) refuse a shop already stored.
UPDATE channel_credentials
SET shop_id = %(shop_id)s, access_token = %(access_token)s, refresh_token = %(refresh_token)s,
    expires_at = %(expires_at)s, updated_at = clock_timestamp()
WHERE client_id = %(client_id)s AND id = %(record_id)s AND channel = 'tiktok_shop'
  AND shop_id IS NULL AND disconnected_at IS NULL
  AND credential_generation = %(generation)s
RETURNING credential_generation;
