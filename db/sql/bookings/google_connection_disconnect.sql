-- Disconnecting: the grant is cleared at once and the row is kept, with its calendar, so a
-- reconnect finds it. The old tokens come back (still encrypted) only so the caller can revoke
-- them at Google; the rows' cleared state is already committed with them.
WITH old AS (
    SELECT id, access_token, refresh_token
    FROM public.booking_calendar_connections
    WHERE client_id = %(client_id)s AND user_id = %(user_id)s
      AND status IN ('connected', 'needs_reconnect')
    FOR UPDATE
)
UPDATE public.booking_calendar_connections c
   SET status = 'pending', access_token = NULL, refresh_token = NULL, token_expires_at = NULL,
       scopes = '{}', updated_at = now()
  FROM old
 WHERE c.client_id = %(client_id)s AND c.id = old.id
RETURNING c.id, c.person_id, old.access_token AS old_access_token,
          old.refresh_token AS old_refresh_token;
