INSERT INTO public.webhook_endpoints (client_id, public_id, credential_id, name)
VALUES (%s, %s, %s, 'Local development Telegram bot')
RETURNING id
