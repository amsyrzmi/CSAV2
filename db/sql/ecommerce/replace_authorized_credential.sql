-- Reconnect: replace the tokens and clear disconnected_at in one UPDATE, so the 0320 fence bumps
-- credential_generation exactly once. Clearing it makes the store count as the shop's active one
-- again, which the global active-shop index (0491) refuses if another client holds the shop now.
UPDATE channel_credentials
SET access_token = %(access_token)s, refresh_token = %(refresh_token)s,
    expires_at = %(expires_at)s, disconnected_at = NULL, updated_at = clock_timestamp()
WHERE client_id = %(client_id)s AND id = %(record_id)s
  AND channel = 'tiktok_shop' AND shop_id = %(shop_id)s
  AND credential_generation = %(generation)s
RETURNING credential_generation;
