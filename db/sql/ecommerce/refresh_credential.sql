-- Verified refresh: store the new encrypted tokens for the exact connected store at one
-- generation. One UPDATE, so the 0320 fence bumps credential_generation exactly once and ends
-- the previous active grant; the caller then inserts the refresh grant at the new generation.
UPDATE channel_credentials
SET access_token = %(access_token)s, refresh_token = %(refresh_token)s,
    expires_at = %(expires_at)s, updated_at = clock_timestamp()
WHERE client_id = %(client_id)s AND id = %(record_id)s AND channel = 'tiktok_shop'
  AND shop_id = %(shop_id)s AND disconnected_at IS NULL
  AND credential_generation = %(generation)s
RETURNING credential_generation;
