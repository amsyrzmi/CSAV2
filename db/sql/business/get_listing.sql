-- A listing cursor: must be this tenant's TikTok listing (and in the store filter, if any).
SELECT id FROM public.marketplace_listings
WHERE client_id = %s AND id = %s AND channel = 'tiktok_shop'
  AND (%s::uuid IS NULL OR store_id = %s);
