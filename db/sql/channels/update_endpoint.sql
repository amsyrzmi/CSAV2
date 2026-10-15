UPDATE public.webhook_endpoints
SET credential_id = %s,
    name = %s,
    is_enabled = true
WHERE public_id = %s
  AND client_id = %s
RETURNING id;
