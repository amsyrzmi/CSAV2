INSERT INTO public.webhook_endpoints
    (id, client_id, public_id, credential_id, name, is_enabled, disabled_reason)
VALUES (%s, %s, %s, %s, 'Private console sandbox', false, 'internal_web_only');
