INSERT INTO public.contacts (client_id, telegram_id, full_name)
VALUES (%s, %s, %s)
ON CONFLICT (client_id, telegram_id) DO NOTHING
RETURNING id
