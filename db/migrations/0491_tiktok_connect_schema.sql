-- 0491_tiktok_connect_schema.sql — TikTok Shop connect: one active store per shop, route lookup, sync state.
-- Preconditions: 0490 applied (legacy unverified stores removed, so the unique index can build);
--   0310 ecommerce_grants, 0320 credential fence, 0470 resolve_whatsapp_route pattern.
-- Rollback: DROP TABLE public.marketplace_store_state;
--   DROP FUNCTION public.resolve_tiktok_shop_route(text);
--   DROP INDEX public.channel_credentials_tiktok_shop_key;
--   Leave the widened ecommerce_grants_source_check in place: grants and their events are
--   referenced by composite foreign keys and are lifecycle evidence, so they are never deleted.
--   Only if no 'verified_token_refresh' row exists may the old check be restored, with
--   ALTER TABLE public.ecommerce_grants DROP CONSTRAINT ecommerce_grants_source_check,
--     ADD CONSTRAINT ecommerce_grants_source_check CHECK (source = 'verified_authorization_exchange')
--     NOT VALID;  (NOT VALID keeps any existing refresh rows from blocking the change.)
--   ALTER TABLE public.channel_credentials DROP COLUMN disconnected_at;
-- RLS: marketplace_store_state gets tenant_isolation as in 0280; no FORCE. The route function is
--   SECURITY DEFINER and owned by csa so the definer bypass works under non-FORCE RLS.

-- A disconnected store keeps its row (orders and listings stay readable for the retention window)
-- but stops counting as the shop's active store. Setting this is a content change, so the 0320
-- fence bumps credential_generation and disconnects active grants, which is wanted.
ALTER TABLE public.channel_credentials ADD COLUMN disconnected_at timestamptz;

-- One TikTok shop is connected to at most one tenant across the deployment, while active.
CREATE UNIQUE INDEX channel_credentials_tiktok_shop_key
    ON public.channel_credentials (shop_id)
    WHERE channel = 'tiktok_shop' AND shop_id IS NOT NULL AND disconnected_at IS NULL;

-- A grant can also be re-verified by a successful token refresh, not only by the authorization exchange.
ALTER TABLE public.ecommerce_grants DROP CONSTRAINT ecommerce_grants_source_check;
ALTER TABLE public.ecommerce_grants ADD CONSTRAINT ecommerce_grants_source_check
    CHECK (source IN ('verified_authorization_exchange', 'verified_token_refresh'));

-- Pre-tenant route lookup for the TikTok webhook: shop id to (client, store), active stores only.
-- Like resolve_whatsapp_route (0470) it never returns token or cipher material.
CREATE OR REPLACE FUNCTION public.resolve_tiktok_shop_route(p_shop_id text)
RETURNS TABLE(client_id uuid, store_id uuid)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT c.client_id, c.id
  FROM channel_credentials AS c
  WHERE c.channel = 'tiktok_shop' AND c.shop_id = p_shop_id AND c.disconnected_at IS NULL
  LIMIT 1;
$$;

ALTER FUNCTION public.resolve_tiktok_shop_route(text) OWNER TO csa;
REVOKE ALL ON FUNCTION public.resolve_tiktok_shop_route(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.resolve_tiktok_shop_route(text) TO csa_app;

-- Sync state lives beside the credential, not on it: every content change on channel_credentials
-- bumps credential_generation and disconnects active grants (0320), and a sync tick must not.
CREATE TABLE public.marketplace_store_state (
    store_id uuid PRIMARY KEY REFERENCES public.channel_credentials(id) ON DELETE CASCADE,
    client_id uuid NOT NULL REFERENCES public.clients(id),
    orders_synced_at timestamptz,
    products_synced_at timestamptz,
    -- The last provider update_time seen; catch-up reads orders changed after it.
    catch_up_cursor timestamptz,
    -- The backfill's provider page token, checkpointed so an interrupted backfill resumes.
    backfill_cursor text,
    last_error_code text,
    problem_notified_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.marketplace_store_state ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON public.marketplace_store_state
    USING (client_id = nullif(current_setting('app.client_id', true), '')::uuid)
    WITH CHECK (client_id = nullif(current_setting('app.client_id', true), '')::uuid);
GRANT SELECT, INSERT, UPDATE, DELETE ON public.marketplace_store_state TO csa_app;
GRANT SELECT ON public.marketplace_store_state TO csa_ro;
