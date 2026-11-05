-- What a refresh starts from: the store's encrypted refresh token, its active grant at the
-- current generation and that grant's shop binding. Read without locks: refresh_store then
-- fences the app, the credential at this generation and the grant before using any of it.
SELECT c.shop_id, c.credential_generation, c.refresh_token AS refresh_token_ciphertext,
       g.id AS grant_id, b.shop_cipher AS shop_cipher_ciphertext
FROM channel_credentials c
JOIN ecommerce_grants g ON g.client_id = c.client_id AND g.record_id = c.id
  AND g.platform = c.channel AND g.shop_id = c.shop_id
  AND g.credential_generation = c.credential_generation AND g.state = 'active'
JOIN ecommerce_shop_bindings b ON b.client_id = g.client_id AND b.grant_id = g.id
WHERE c.client_id = %(client_id)s AND c.id = %(record_id)s AND c.channel = 'tiktok_shop'
  AND c.shop_id IS NOT NULL AND c.disconnected_at IS NULL AND c.refresh_token IS NOT NULL;
