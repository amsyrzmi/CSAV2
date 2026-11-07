-- The callback's result: the encrypted grant and the calendar. A new refresh token replaces the old
-- one; Google sends none when it is unchanged, and then the stored one stays.
UPDATE public.booking_calendar_connections
   SET status = 'connected',
       access_token = %(access_token)s,
       refresh_token = coalesce(%(refresh_token)s, refresh_token),
       token_expires_at = %(expires_at)s,
       scopes = %(scopes)s,
       calendar_id = %(calendar_id)s,
       updated_at = now()
 WHERE client_id = %(client_id)s AND id = %(id)s AND user_id = %(user_id)s
RETURNING id;
