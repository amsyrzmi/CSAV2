WITH gone AS (
    UPDATE public.channel_credentials
    SET access_token = NULL, shop_id = NULL,
        meta = (coalesce(meta, '{}'::jsonb) - 'telegram_webhook_secret_enc')
               - 'telegram_webhook_registered_at',
        disconnected_at = now(), updated_at = now()
    WHERE id = %s AND client_id = %s
    RETURNING id
)
UPDATE public.webhook_endpoints
SET is_enabled = false, disabled_reason = 'disconnected', updated_at = now()
WHERE client_id = %s AND credential_id IN (SELECT id FROM gone)
RETURNING credential_id;
