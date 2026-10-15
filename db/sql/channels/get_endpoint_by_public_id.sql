SELECT id, client_id, public_id, credential_id, name, is_enabled
FROM public.webhook_endpoints
WHERE public_id = %s;
