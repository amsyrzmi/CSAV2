WITH erased AS (
    UPDATE public.channel_credentials
    SET access_token = NULL, updated_at = now()
    WHERE client_id = %s AND channel = 'whatsapp'
    RETURNING id
)
UPDATE public.webhook_endpoints
SET is_enabled = false, disabled_reason = 'disconnected', updated_at = now()
WHERE client_id = %s AND credential_id IN (SELECT id FROM erased)
RETURNING credential_id;
