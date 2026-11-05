-- TikTok stores a refused refresh marked expired (mark_store_expired.sql: both tokens cleared,
-- expires_at set to the refusal time) that nobody reconnected for 30 days, so they are due to
-- become disconnected with reason 'expired'. A reconnect writes tokens again and so leaves this
-- list; a store that still holds tokens (a refresh that keeps failing, not a refusal) is not
-- listed. Rows are locked (SKIP LOCKED) so a reconnect in flight (it holds the row lock through
-- its TikTok calls) is skipped; disconnect_store.sql re-tests the same state as a second guard.
-- A pending store (no shop) and a disconnected one never match.
SELECT c.id
FROM public.channel_credentials c
WHERE c.client_id = %(client_id)s AND c.channel = 'tiktok_shop'
  AND c.shop_id IS NOT NULL AND c.shop_id <> '' AND c.disconnected_at IS NULL
  AND c.access_token IS NULL AND c.refresh_token IS NULL
  AND c.expires_at <= %(expired_cutoff)s
ORDER BY c.expires_at, c.id
LIMIT %(batch_size)s
FOR UPDATE OF c SKIP LOCKED;
