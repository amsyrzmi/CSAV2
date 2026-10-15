INSERT INTO public.webhook_endpoints
    (id, client_id, public_id, credential_id, name, is_enabled)
VALUES (%s, %s, %s, %s, %s, true)
RETURNING id;
