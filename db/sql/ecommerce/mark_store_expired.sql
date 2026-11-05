-- TikTok refused the refresh, or the fresh shop no longer matched: the store needs reconnecting.
-- A deliberate content change: the 0320 fence bumps credential_generation and disconnects the
-- active grant, so nothing can read with the old tokens. The tokens are cleared (a reconnect
-- writes new ones) and expires_at is set to now, which store_connections.sql reads as 'expired'.
UPDATE channel_credentials
SET access_token = NULL, refresh_token = NULL, expires_at = clock_timestamp(),
    updated_at = clock_timestamp()
WHERE client_id = %(client_id)s AND id = %(record_id)s AND channel = 'tiktok_shop'
  AND shop_id = %(shop_id)s AND disconnected_at IS NULL
  AND credential_generation = %(generation)s
RETURNING credential_generation;
