-- The webhook's pre-tenant lookup: a TikTok shop id to its active store (client_id, store_id).
-- resolve_tiktok_shop_route (migration 0491) is SECURITY DEFINER, so this needs no tenant
-- context; it returns no row for an unknown shop or a disconnected store.
SELECT client_id, store_id FROM public.resolve_tiktok_shop_route(%(shop_id)s);
