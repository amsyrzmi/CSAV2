UPDATE public.channel_credentials
SET meta = meta || %s, updated_at = now()
WHERE id = %s AND client_id = %s
RETURNING id;
