-- Delete up to batch_size listings of stores disconnected at or before disconnect_cutoff
-- (30 days). A store that reconnected has disconnected_at NULL and keeps its listings.
DELETE FROM public.marketplace_listings l
WHERE l.id IN (
    SELECT m.id
    FROM public.marketplace_listings m
    JOIN public.channel_credentials c ON c.id = m.store_id AND c.client_id = m.client_id
    WHERE m.client_id = %(client_id)s AND c.channel = 'tiktok_shop'
      AND c.disconnected_at <= %(disconnect_cutoff)s
    ORDER BY m.id
    LIMIT %(batch_size)s
    FOR UPDATE OF m SKIP LOCKED
)
RETURNING l.id, l.store_id;
