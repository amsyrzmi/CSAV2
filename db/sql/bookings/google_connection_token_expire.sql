-- Google refused the access token: it is treated as expired, so the next call refreshes it (and
-- finds the grant revoked, if it was).
UPDATE public.booking_calendar_connections
   SET token_expires_at = now(), updated_at = now()
 WHERE client_id = %(client_id)s AND person_id = %(person_id)s AND status = 'connected';
