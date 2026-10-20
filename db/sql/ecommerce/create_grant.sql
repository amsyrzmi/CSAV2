INSERT INTO ecommerce_grants
    (id, client_id, record_id, platform, shop_id, app_id, app_revision, credential_generation,
     revision, state, source, scopes, verified_at, expires_at)
SELECT %(id)s, c.client_id, c.id, c.channel, c.shop_id, %(app_id)s, %(app_revision)s,
       %(credential_generation)s, 1, 'active', 'verified_authorization_exchange',
       %(scopes)s, %(verified_at)s, %(expires_at)s
FROM channel_credentials c
WHERE c.client_id = %(client_id)s AND c.id = %(record_id)s
  AND c.channel = 'tiktok_shop' AND c.shop_id = %(shop_id)s
  AND c.credential_generation = %(credential_generation)s
ON CONFLICT DO NOTHING
RETURNING id;
