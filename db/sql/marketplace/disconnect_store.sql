-- Disconnect one TikTok store of the caller's tenant: set disconnected_at and clear both
-- encrypted tokens in ONE update, so the 0320 fence bumps credential_generation exactly once and
-- disconnects the active grant. A pending store (no shop) is not a connected store and is never
-- touched. A store that is already disconnected is found but not changed (idempotent).
-- expired_before (optional, NULL = no guard) is for the expired-to-disconnected conversion: the
-- update then also requires the expired state to still hold on the locked row (both tokens
-- cleared and expires_at at or before it). A reconnect that committed after the caller listed
-- the store writes tokens again, fails the guard, and the store is left alone. The guard is
-- re-checked against the locked, current row version when the lock had to wait.
-- found:        the row is one of this tenant's TikTok stores that has a shop (RLS hides others);
-- changed:      this call disconnected it;
-- disconnected: the store is disconnected now (before this call or by it), so its pending jobs
--               may be cancelled; false when the guard refused, so a reconnected store keeps them.
WITH target AS (
    SELECT c.id, c.disconnected_at
    FROM public.channel_credentials c
    WHERE c.client_id = %(client_id)s AND c.id = %(store_id)s AND c.channel = 'tiktok_shop'
      AND c.shop_id IS NOT NULL AND c.shop_id <> ''
    FOR UPDATE
), changed AS (
    UPDATE public.channel_credentials c
    SET disconnected_at = %(now)s, access_token = NULL, refresh_token = NULL
    FROM target t
    WHERE c.id = t.id AND t.disconnected_at IS NULL
      AND (%(expired_before)s::timestamptz IS NULL
           OR (c.access_token IS NULL AND c.refresh_token IS NULL
               AND c.expires_at <= %(expired_before)s::timestamptz))
    RETURNING c.id
)
SELECT EXISTS (SELECT 1 FROM target) AS found,
       EXISTS (SELECT 1 FROM changed) AS changed,
       EXISTS (SELECT 1 FROM changed) OR EXISTS (
           SELECT 1 FROM target WHERE disconnected_at IS NOT NULL) AS disconnected;
