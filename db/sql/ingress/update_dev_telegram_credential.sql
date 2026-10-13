UPDATE public.channel_credentials
SET access_token = %s,
    meta = %s,
    updated_at = now()
WHERE id = %s
  AND client_id = %s
