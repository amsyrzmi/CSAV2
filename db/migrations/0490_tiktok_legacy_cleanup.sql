-- 0490_tiktok_legacy_cleanup.sql — remove unverified legacy TikTok Shop stores before the global shop index.
-- Preconditions: 0310 (ecommerce_grants) and 0320 (credential fence) applied. Own range 0490–0499.
--   Runs before 0491 creates the unique index on shop_id.
-- Why: the old OAuth callback took client_id from an unauthenticated query parameter, so any
--   channel='tiktok_shop' credential it wrote was never verified. A store is verified only if it has
--   an ecommerce_grants row (any state). None are expected to exist.
-- Rollback: not reversible (rows are deleted). A store that is needed again is connected afresh
--   through the new flow. Ingress events marked 'dead' were never going to be dispatched.
-- No schema changes, no RLS changes. Runs as the migration owner (csa); non-FORCE RLS does not apply.

-- Legacy TikTok webhook events that were never dispatched are retired, not replayed.
UPDATE public.ingress_events
SET status = 'dead', last_error = 'legacy_tiktok_store_removed'
WHERE raw ->> 'channel' = 'tiktok_shop' AND status = 'received';

-- A credential is legacy when it has no grant. The subquery is re-evaluated by each statement and
-- stays true until the credential row itself is deleted last.
-- Rows that point at a doomed endpoint block its deletion (plain foreign keys). They belong to
-- the removed store; outbound_delivery_attempts go with their delivery (ON DELETE CASCADE).
DELETE FROM public.outbound_deliveries
WHERE endpoint_id IN (
    SELECT e.id FROM public.webhook_endpoints AS e
    JOIN public.channel_credentials AS c ON c.id = e.credential_id
    WHERE c.channel = 'tiktok_shop'
      AND NOT EXISTS (SELECT 1 FROM public.ecommerce_grants AS g WHERE g.record_id = c.id));
DELETE FROM public.ingress_events
WHERE endpoint_id IN (
    SELECT e.id FROM public.webhook_endpoints AS e
    JOIN public.channel_credentials AS c ON c.id = e.credential_id
    WHERE c.channel = 'tiktok_shop'
      AND NOT EXISTS (SELECT 1 FROM public.ecommerce_grants AS g WHERE g.record_id = c.id));
DELETE FROM public.webhook_endpoints
WHERE credential_id IN (
    SELECT c.id FROM public.channel_credentials AS c
    WHERE c.channel = 'tiktok_shop'
      AND NOT EXISTS (SELECT 1 FROM public.ecommerce_grants AS g WHERE g.record_id = c.id));
DELETE FROM public.channel_credentials AS c
WHERE c.channel = 'tiktok_shop'
  AND NOT EXISTS (SELECT 1 FROM public.ecommerce_grants AS g WHERE g.record_id = c.id);
