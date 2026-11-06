-- Clear buyer personal data on up to batch_size TikTok orders: ship_to becomes NULL and
-- buyer_cleared_at is set (the order upsert never writes buyer data back once it is set).
-- Number, totals, statuses, dates and items stay. Two scopes, one statement shape:
--   aged          delivered or cancelled orders last updated by TikTok at or before
--                 buyer_cutoff (90 days ago). The snapshot has no delivered/cancelled time, so
--                 provider_updated_at is the clock (spec 6.6, controller ruling). An order
--                 TikTok gave no update time falls back to synced_at (when we stored it), so it
--                 is not kept for ever; with neither time it cannot be aged and is left alone;
--   disconnected  any order of a store disconnected at or before disconnect_cutoff (30 days).
-- Local orders (other channels) are never touched. Rows are locked SKIP LOCKED, oldest first.
WITH due AS (
    SELECT o.id
    FROM public.orders o
    WHERE o.client_id = %(client_id)s AND o.channel = 'tiktok_shop' AND o.buyer_cleared_at IS NULL
      AND (
          (%(scope)s = 'aged' AND o.status IN ('delivered', 'cancelled')
              AND coalesce(o.provider_updated_at, o.synced_at) <= %(buyer_cutoff)s)
          OR (%(scope)s = 'disconnected' AND EXISTS (
              SELECT 1 FROM public.channel_credentials c
              WHERE c.id = o.store_id AND c.client_id = o.client_id
                AND c.disconnected_at <= %(disconnect_cutoff)s))
      )
    ORDER BY coalesce(o.provider_updated_at, o.synced_at) NULLS FIRST, o.id
    LIMIT %(batch_size)s
    FOR UPDATE OF o SKIP LOCKED
)
UPDATE public.orders o
SET ship_to = NULL, buyer_cleared_at = %(now)s, updated_at = now()
FROM due
WHERE o.id = due.id
RETURNING o.id;
