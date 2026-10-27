SELECT endpoint_id, client_id, credential_id, rate_limit_rpm, is_enabled, disabled_reason,
       access_token, meta, runtime, n8n_webhook_url, deployment_mode
FROM public.resolve_whatsapp_route(%s)
ORDER BY is_enabled DESC, endpoint_id
LIMIT 1
