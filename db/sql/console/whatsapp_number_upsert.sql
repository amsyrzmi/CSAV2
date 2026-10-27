WITH cred AS (
    INSERT INTO public.channel_credentials (id, client_id, channel, shop_id, access_token, meta)
    VALUES (%s, %s, 'whatsapp', %s, %s, %s)
    ON CONFLICT (id) DO UPDATE
        SET shop_id = EXCLUDED.shop_id, access_token = EXCLUDED.access_token,
            meta = EXCLUDED.meta, updated_at = now()
    RETURNING id
)
INSERT INTO public.webhook_endpoints (client_id, public_id, credential_id, name, is_enabled)
SELECT %s, %s, id, %s, true FROM cred
ON CONFLICT (credential_id) DO UPDATE
    SET name = EXCLUDED.name, is_enabled = true, disabled_reason = NULL, updated_at = now()
RETURNING credential_id;
