-- 0470_whatsapp_channel.sql — WhatsApp Cloud API channel: schema and pre-tenant route lookup.
-- Preconditions: 0460 applied (0120 resolve_endpoint, 0310 channel_credentials.shop_id).
-- Rollback: DROP FUNCTION public.resolve_whatsapp_route(text);
--   DROP INDEX public.channel_credentials_whatsapp_number_key;
--   DROP INDEX public.outbound_deliveries_provider_message_idx;
--   ALTER TABLE public.outbound_deliveries DROP COLUMN provider_status, DROP COLUMN provider_message_id;
--   ALTER TABLE public.conversations DROP COLUMN last_inbound_at;
--   (first DELETE any channel='whatsapp' credentials and conversations).
-- No RLS changes: every touched table already carries tenant_isolation (0110).
ALTER TABLE public.conversations ADD COLUMN last_inbound_at timestamptz;

ALTER TABLE public.outbound_deliveries
    ADD COLUMN provider_message_id text,
    ADD COLUMN provider_status text CHECK (provider_status IN ('sent','delivered','read','failed'));

CREATE INDEX outbound_deliveries_provider_message_idx
    ON public.outbound_deliveries (client_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL;

-- One WhatsApp phone number id belongs to exactly one tenant across the deployment.
CREATE UNIQUE INDEX channel_credentials_whatsapp_number_key
    ON public.channel_credentials (shop_id) WHERE channel = 'whatsapp';

-- Pre-tenant route lookup by phone number id. Like resolve_endpoint (0120) it is
-- SECURITY DEFINER and owned by csa so the definer bypass works under non-FORCE RLS.
-- The access token is returned still encrypted; the caller decrypts it with the
-- credential AAD.
CREATE OR REPLACE FUNCTION public.resolve_whatsapp_route(p_phone_number_id text)
RETURNS TABLE(
  endpoint_id uuid,
  client_id uuid,
  credential_id uuid,
  rate_limit_rpm int,
  is_enabled boolean,
  disabled_reason text,
  access_token text,
  meta jsonb,
  runtime text,
  n8n_webhook_url text,
  deployment_mode text
)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
  SELECT e.id, e.client_id, c.id, e.rate_limit_rpm, e.is_enabled, e.disabled_reason,
         c.access_token, c.meta,
         tenant.config ->> 'runtime',
         tenant.config ->> 'n8n_webhook_url',
         COALESCE(tenant.config -> 'policy' ->> 'deployment_mode', tenant.deployment_mode)
  FROM channel_credentials AS c
  JOIN webhook_endpoints AS e ON e.credential_id = c.id AND e.client_id = c.client_id
  JOIN clients AS tenant ON tenant.id = e.client_id
  WHERE c.channel = 'whatsapp' AND c.shop_id = p_phone_number_id
  ORDER BY e.is_enabled DESC, e.id
  LIMIT 1;
$$;

ALTER FUNCTION public.resolve_whatsapp_route(text) OWNER TO csa;
REVOKE ALL ON FUNCTION public.resolve_whatsapp_route(text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.resolve_whatsapp_route(text) TO csa_app;
