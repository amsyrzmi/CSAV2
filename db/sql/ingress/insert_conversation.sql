INSERT INTO public.conversations (client_id, contact_id, channel, channel_thread)
VALUES (%s, %s, %s, %s)
ON CONFLICT (client_id, channel, channel_thread) DO NOTHING
RETURNING id, contact_id, status, turn_count
