-- The tenant's TikTok stores (or one store) with their sync state and stored sync progress.
-- sync_state:
--   pending       no shop yet (a first connect that has not come back);
--   disconnected  disconnected on purpose or by a deauthorization;
--   expired       tokens cleared by a refused refresh, the access token or grant lapsed, or the
--                 grant was revoked: it needs a reconnect;
--   connected     an active grant at the current generation, under the active app revision,
--                 with its shop binding: reads can run (grant_id is set only in this state);
--   unavailable   anything else (for example an app rotation the next refresh will carry).
-- With connected_only, only connected stores come back.
SELECT t.store_id, t.client_id, t.shop_id, t.sync_state,
       CASE WHEN t.sync_state = 'connected' THEN t.latest_grant_id END AS grant_id,
       t.orders_synced_at, t.products_synced_at, t.catch_up_cursor, t.backfill_cursor,
       t.last_error_code, t.problem_notified_at
FROM (
    SELECT c.id AS store_id, c.client_id, c.shop_id, g.id AS latest_grant_id,
           CASE
               WHEN c.shop_id IS NULL OR c.shop_id = '' THEN 'pending'
               WHEN c.disconnected_at IS NOT NULL THEN 'disconnected'
               WHEN c.access_token IS NULL
                   OR c.expires_at <= clock_timestamp()
                   OR g.state = 'revoked'
                   OR (g.state = 'active' AND g.expires_at <= clock_timestamp()) THEN 'expired'
               WHEN g.state = 'active'
                   AND g.credential_generation = c.credential_generation
                   AND g.verified_at <= clock_timestamp()
                   AND c.expires_at IS NOT NULL
                   AND a.state = 'active' AND a.revision = g.app_revision
                   AND EXISTS (
                       SELECT 1 FROM ecommerce_shop_bindings b
                       WHERE b.client_id = c.client_id AND b.grant_id = g.id
                   ) THEN 'connected'
               ELSE 'unavailable'
           END AS sync_state,
           s.orders_synced_at, s.products_synced_at, s.catch_up_cursor, s.backfill_cursor,
           s.last_error_code, s.problem_notified_at
    FROM channel_credentials c
    LEFT JOIN LATERAL (
        SELECT id, app_id, app_revision, state, credential_generation, expires_at, verified_at
        FROM ecommerce_grants
        WHERE client_id = c.client_id AND record_id = c.id
          AND platform = c.channel AND shop_id = c.shop_id
        ORDER BY credential_generation DESC, verified_at DESC, id DESC
        LIMIT 1
    ) g ON true
    LEFT JOIN ecommerce_apps a ON a.app_id = g.app_id
    LEFT JOIN marketplace_store_state s ON s.store_id = c.id AND s.client_id = c.client_id
    WHERE c.client_id = %(client_id)s AND c.channel = 'tiktok_shop'
      AND (%(store_id)s::uuid IS NULL OR c.id = %(store_id)s::uuid)
) t
WHERE NOT %(connected_only)s::boolean OR t.sync_state = 'connected'
ORDER BY t.store_id;
