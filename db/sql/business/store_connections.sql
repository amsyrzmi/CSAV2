SELECT c.id,
       CASE
           WHEN c.channel <> 'tiktok_shop' THEN 'unsupported'
           WHEN c.shop_id IS NULL OR c.shop_id = '' THEN 'missing_shop_id'
           WHEN g.state = 'revoked' THEN 'revoked'
           -- A refused refresh (mark_store_expired.sql) ends the grant through the credential
           -- fence and sets expires_at to that moment: the store needs reconnecting, so it reads
           -- as expired. A store disconnected on purpose (disconnected_at) still reads disconnected.
           WHEN g.state = 'disconnected' AND c.disconnected_at IS NULL
               AND c.expires_at <= statement_timestamp() THEN 'expired'
           WHEN g.state = 'disconnected' THEN 'disconnected'
           WHEN c.expires_at <= statement_timestamp() THEN 'expired'
           WHEN g.id IS NULL THEN 'unverified'
           WHEN g.credential_generation <> c.credential_generation THEN 'disconnected'
           WHEN a.app_id IS NULL OR a.state <> 'active' OR a.revision <> g.app_revision
               THEN 'app_unavailable'
           WHEN g.expires_at <= statement_timestamp() THEN 'expired'
           WHEN g.verified_at > statement_timestamp() OR c.expires_at IS NULL THEN 'unverified'
           WHEN NOT EXISTS (
               SELECT 1 FROM ecommerce_shop_bindings b
               WHERE b.client_id = c.client_id AND b.grant_id = g.id
           ) THEN 'binding_missing'
           WHEN NOT (g.scopes && ARRAY[
               'seller.order.info', 'seller.fs.order&fufillment.management'
           ] AND g.scopes && ARRAY[
               'seller.authorization.info', 'seller.fs.order&fufillment.management'
           ]) OR NOT (g.scopes && %(product_scopes)s::text[]) THEN 'permissions_missing'
           ELSE 'authorization_recorded'
       END AS status,
       -- Sync progress, TikTok stores only. A failure counts once a notice is open
       -- (problem_notified_at); the raw error code stays server side.
       c.channel = 'tiktok_shop' AS is_tiktok,
       st.orders_synced_at, st.products_synced_at,
       (st.problem_notified_at IS NOT NULL) AS sync_failing
FROM channel_credentials c
LEFT JOIN LATERAL (
    SELECT id, app_id, app_revision, state, credential_generation,
           expires_at, verified_at, scopes
    FROM ecommerce_grants
    WHERE client_id = c.client_id AND record_id = c.id
      AND platform = c.channel AND shop_id = c.shop_id
    ORDER BY credential_generation DESC, verified_at DESC, id DESC
    LIMIT 1
) g ON true
LEFT JOIN ecommerce_apps a ON a.app_id = g.app_id
LEFT JOIN marketplace_store_state st ON st.store_id = c.id AND st.client_id = c.client_id
WHERE c.client_id = %(client_id)s AND c.id = ANY(%(ids)s::uuid[]);
