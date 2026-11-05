-- TikTok stores to refresh: connected (a shop, not disconnected, a refresh token) with an active
-- grant at the current credential generation under an active app, and either an access token
-- that expires within the window or a grant made under an older app revision. Grants never
-- change revision, so after a secret rotation the refresh makes a new grant at the new one.
-- Pending stores (no shop), disconnected or expired ones (no active grant) never appear.
SELECT c.id
FROM channel_credentials c
JOIN ecommerce_grants g ON g.client_id = c.client_id AND g.record_id = c.id
  AND g.platform = c.channel AND g.shop_id = c.shop_id
  AND g.credential_generation = c.credential_generation AND g.state = 'active'
JOIN ecommerce_apps a ON a.app_id = g.app_id AND a.state = 'active'
WHERE c.client_id = %(client_id)s AND c.channel = 'tiktok_shop'
  AND c.shop_id IS NOT NULL AND c.disconnected_at IS NULL AND c.refresh_token IS NOT NULL
  AND (c.expires_at <= clock_timestamp() + %(within)s OR g.app_revision <> a.revision)
ORDER BY c.expires_at, c.id;
