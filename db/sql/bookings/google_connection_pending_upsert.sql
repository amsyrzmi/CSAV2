-- Starting to connect: a member's one connection row, made pending the first time and left as it is
-- (tokens, calendar) when they connect again, so a reconnect finds its calendar. Its person is the
-- one they are linked to now.
INSERT INTO public.booking_calendar_connections (client_id, user_id, person_id, status)
VALUES (%(client_id)s, %(user_id)s, %(person_id)s, 'pending')
ON CONFLICT (client_id, user_id) DO UPDATE
   SET person_id = EXCLUDED.person_id, updated_at = now()
RETURNING id, status, calendar_id;
