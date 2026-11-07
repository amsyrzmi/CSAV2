-- Google refused the refresh token: nothing more is tried until the person connects again.
UPDATE public.booking_calendar_connections
   SET status = 'needs_reconnect', updated_at = now()
 WHERE client_id = %(client_id)s AND id = %(id)s AND status = 'connected'
RETURNING id;
