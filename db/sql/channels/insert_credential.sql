INSERT INTO public.channel_credentials
    (id, client_id, channel, shop_id, access_token, refresh_token, expires_at, meta)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
RETURNING id;
