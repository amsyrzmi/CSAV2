SELECT b.grant_id, b.shop_cipher, c.access_token AS access_token_ciphertext, c.expires_at
FROM channel_credentials c
JOIN ecommerce_grants g ON g.client_id = c.client_id AND g.record_id = c.id
JOIN ecommerce_shop_bindings b ON b.client_id = g.client_id AND b.grant_id = g.id
JOIN ecommerce_apps a ON a.app_id = g.app_id AND a.revision = g.app_revision
  AND a.state = 'active'
WHERE c.client_id = %(client_id)s AND c.id = %(record_id)s
  AND c.channel = 'tiktok_shop' AND c.shop_id = %(shop_id)s
  AND c.credential_generation = %(credential_generation)s
  AND g.platform = c.channel AND g.shop_id = c.shop_id AND g.app_id = %(app_id)s
  AND g.app_revision = %(app_revision)s
  AND g.credential_generation = c.credential_generation
  AND g.state = 'active' AND g.verified_at <= clock_timestamp()
  AND g.expires_at > clock_timestamp()
  AND c.access_token IS NOT NULL AND c.expires_at > clock_timestamp();
