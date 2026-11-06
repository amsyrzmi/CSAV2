-- The tenant's pending TikTok store (no shop yet), reused by Connect so clicks do not pile up
-- pending rows. The oldest is chosen when a race left more than one; the purge job removes
-- the rest (spec 6.6).
SELECT id, credential_generation
FROM channel_credentials
WHERE client_id = %(client_id)s AND channel = 'tiktok_shop'
  AND shop_id IS NULL AND disconnected_at IS NULL
ORDER BY updated_at, id
LIMIT 1;
