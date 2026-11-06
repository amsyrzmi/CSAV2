-- The write gate before each page is stored: the store still has this shop, is not
-- disconnected and still has tokens. FOR SHARE makes a concurrent disconnect wait for this
-- transaction (or this one see it), so nothing is written after a disconnect.
SELECT c.id
FROM public.channel_credentials c
WHERE c.client_id = %(client_id)s AND c.id = %(store_id)s AND c.channel = 'tiktok_shop'
  AND c.shop_id = %(shop_id)s AND c.disconnected_at IS NULL AND c.access_token IS NOT NULL
FOR SHARE;
