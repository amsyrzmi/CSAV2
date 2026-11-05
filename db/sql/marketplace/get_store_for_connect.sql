-- One of the tenant's own TikTok stores, for Reconnect and Disconnect: the fields the fences
-- need, never a token. Another tenant's store, or another channel's row, is not found.
SELECT id, shop_id, credential_generation, disconnected_at
FROM channel_credentials
WHERE client_id = %(client_id)s AND id = %(store_id)s AND channel = 'tiktok_shop';
