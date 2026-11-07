-- A refreshed access token, stored encrypted by the caller.
UPDATE public.booking_calendar_connections
   SET access_token = %(access_token)s, token_expires_at = %(expires_at)s, updated_at = now()
 WHERE client_id = %(client_id)s AND id = %(id)s AND status = 'connected'
RETURNING id;
