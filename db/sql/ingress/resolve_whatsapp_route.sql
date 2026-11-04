SELECT endpoint_id, client_id, credential_id, rate_limit_rpm, is_enabled, disabled_reason,
       meta
FROM public.resolve_whatsapp_route(%s)
ORDER BY is_enabled DESC, endpoint_id
LIMIT 1
