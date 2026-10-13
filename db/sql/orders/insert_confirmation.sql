INSERT INTO public.order_confirmations
    (client_id, order_id, contact_id, token, channel, state, expires_at)
VALUES (%s, %s, %s, %s, %s, 'pending', %s)
ON CONFLICT (client_id, token) DO NOTHING
RETURNING id;
