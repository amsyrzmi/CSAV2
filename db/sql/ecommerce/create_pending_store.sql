-- A pending TikTok store: no shop yet. INSERT does not fire the 0320 fence, so it starts at
-- credential_generation 1. The row is written only for the transaction's own tenant (no row is
-- returned otherwise), and RLS WITH CHECK keeps it there as well.
INSERT INTO channel_credentials (id, client_id, channel, shop_id)
SELECT %(id)s, %(client_id)s, 'tiktok_shop', NULL
WHERE %(client_id)s::uuid = nullif(current_setting('app.client_id', true), '')::uuid
RETURNING id;
