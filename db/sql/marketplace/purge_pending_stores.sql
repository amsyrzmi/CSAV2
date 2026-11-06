-- Delete up to batch_size abandoned pending TikTok stores (spec 6.2 and 6.6): no shop, never
-- disconnected, last touched at or before pending_cutoff (1 day), with no live authorization
-- attempt. An attempt is live while it is 'pending' or 'consumed' and has not expired. The
-- guard trigger freezes an expired attempt at 'pending', so status alone cannot tell; and a
-- 'consumed' one (the callback's first transaction ran, the exchange is under way) can still
-- be completed until it expires, so the store must stay for that window. Attempts carry
-- the store id in their JSON context and have no foreign key to the store, so they are never
-- deleted (csa_app cannot) and nothing blocks the delete. A row that ever held a grant is kept:
-- grants are lifecycle evidence. The delete cascades to the store's sync jobs, listings and
-- sync state and sets orders.store_id NULL; the 0320 fence fires on DELETE with no grant to end.
-- A first connect completing right now holds the row lock, so SKIP LOCKED leaves it alone.
DELETE FROM public.channel_credentials d
WHERE d.id IN (
    SELECT c.id
    FROM public.channel_credentials c
    WHERE c.client_id = %(client_id)s AND c.channel = 'tiktok_shop'
      AND c.shop_id IS NULL AND c.disconnected_at IS NULL
      AND c.updated_at <= %(pending_cutoff)s
      AND NOT EXISTS (
          SELECT 1 FROM public.ecommerce_grants g
          WHERE g.client_id = c.client_id AND g.record_id = c.id)
      AND NOT EXISTS (
          SELECT 1 FROM public.ecommerce_authorization_attempts a
          WHERE a.client_id = c.client_id AND a.status IN ('pending', 'consumed')
            AND a.expires_at > %(now)s
            AND a.context -> 'binding' ->> 'record_id' = c.id::text)
    ORDER BY c.updated_at, c.id
    LIMIT %(batch_size)s
    FOR UPDATE OF c SKIP LOCKED
)
RETURNING d.id;
