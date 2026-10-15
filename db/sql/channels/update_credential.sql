UPDATE public.channel_credentials
SET access_token = %s,
    refresh_token = %s,
    expires_at = %s,
    meta = %s,
    updated_at = now()
WHERE id = %s
  AND client_id = %s
RETURNING id;
